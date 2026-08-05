# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Aggregation policies, final-root accountability, and arrival-order independence."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.adapters.mock import MockSubstratePort
from agentic.agentic_layer.aggregation import (
    AllPolicy,
    BestEffortPolicy,
    QuorumPolicy,
    default_steer_heads,
    fail_closed_if_unsatisfiable,
    maybe_complete,
    parse_aggregation_policy,
    recompute_trace_root,
    verify_claimed_root,
    verify_leaf_inclusion,
)
from agentic.agentic_layer.context_pit import ContextPIT
from agentic.port.errors import AggregationFailed
from agentic.port.events import ResponseArrived
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key
from agentic.trust.merkle import NULL_RESPONSE, NULL_STEER, PENDING


def _issuer_der(key) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _digest(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def _commit(
    pit: ContextPIT,
    issuer,
    parent: bytes,
    labels: list[str],
    *,
    policy: str = "ALL",
) -> None:
    specs = [
        (_digest(label), f"p-{label}".encode(), 100, ("cap",), b"cred")
        for label in labels
    ]
    pit.commit(
        parent_intent_digest=parent,
        issuer_public_key_der=_issuer_der(issuer),
        aggregation_policy=policy,
        leaf_specs=specs,
        now_ms=0,
    )


def test_parse_policies() -> None:
    assert isinstance(parse_aggregation_policy("ALL"), AllPolicy)
    assert isinstance(parse_aggregation_policy("BEST_EFFORT"), BestEffortPolicy)
    q = parse_aggregation_policy("QUORUM(2,3)")
    assert isinstance(q, QuorumPolicy)
    assert q.k == 2 and q.n == 3
    with pytest.raises(ValueError):
        parse_aggregation_policy("MAJORITY")


def test_all_completes_when_every_leaf_resolved() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x01" * 32
    _commit(pit, issuer, parent, ["a", "b"])
    assert maybe_complete(pit, parent, now_ms=1) is None
    pit.record_response(parent, 0, _digest("ra"))
    assert maybe_complete(pit, parent, now_ms=2) is None
    pit.record_response(parent, 1, _digest("rb"))
    entry = maybe_complete(pit, parent, now_ms=3)
    assert entry is not None
    assert entry.terminated
    assert all(leaf.response_digest != PENDING for leaf in entry.leaves)


def test_quorum_completes_and_nulls_remainder() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x02" * 32
    _commit(pit, issuer, parent, ["a", "b", "c"], policy="QUORUM(2,3)")
    pit.record_response(parent, 0, _digest("ra"))
    pit.record_response(parent, 1, _digest("rb"))
    entry = maybe_complete(pit, parent, now_ms=5)
    assert entry is not None and entry.terminated
    assert entry.leaves[2].response_digest == NULL_RESPONSE
    assert entry.leaves[0].response_digest == _digest("ra")


def test_quorum_unsatisfiable_raises() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x03" * 32
    _commit(pit, issuer, parent, ["a", "b", "c"], policy="QUORUM(2,3)")
    pit.record_timeout(parent, 0)
    pit.record_timeout(parent, 1)
    entry = pit.get(parent)
    assert entry is not None
    with pytest.raises(AggregationFailed):
        fail_closed_if_unsatisfiable(entry)


def test_best_effort_waits_for_deadline_then_nulls() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x04" * 32
    _commit(pit, issuer, parent, ["a", "b"], policy="BEST_EFFORT")
    pit.record_response(parent, 0, _digest("ra"))
    assert maybe_complete(pit, parent, now_ms=1) is None
    entry = maybe_complete(pit, parent, now_ms=2, deadline_reached=True)
    assert entry is not None and entry.terminated
    assert entry.leaves[0].response_digest == _digest("ra")
    assert entry.leaves[1].response_digest == NULL_RESPONSE


def test_verifier_detects_forged_contribution() -> None:
    expected = (_digest("a"), _digest("b"))
    steers = default_steer_heads(2)
    honest = (_digest("ra"), _digest("rb"))
    root = recompute_trace_root(expected, steers, honest)
    forged = (_digest("FORGED"), _digest("rb"))
    assert verify_claimed_root(expected, steers, honest, root)
    assert not verify_claimed_root(expected, steers, forged, root)
    assert verify_leaf_inclusion(expected, steers, honest, 0, root)
    assert not verify_leaf_inclusion(expected, steers, forged, 0, root)


def test_verifier_detects_wrongly_claimed_omission() -> None:
    """Claiming NULL for a leaf that actually contributed fails the root check."""
    expected = (_digest("a"), _digest("b"))
    steers = default_steer_heads(2)
    actual = (_digest("ra"), _digest("rb"))
    root = recompute_trace_root(expected, steers, actual)
    wrongly_omitted = (_digest("ra"), NULL_RESPONSE)
    assert not verify_claimed_root(expected, steers, wrongly_omitted, root)
    # Honest omission is accountable: NULL leaf verifies against its own root.
    omission_root = recompute_trace_root(expected, steers, wrongly_omitted)
    assert verify_leaf_inclusion(expected, steers, wrongly_omitted, 1, omission_root)


@pytest.mark.asyncio
async def test_out_of_order_delivery_same_trace_root() -> None:
    """Invariant I4: arrival order must not change the final root."""
    issuer = generate_ed25519_private_key()
    labels = ["a", "b", "c"]
    responses = {label: f"resp-{label}".encode() for label in labels}

    async def run(order: list[str]) -> bytes:
        pit = ContextPIT()
        parent = b"\x10" * 32
        _commit(pit, issuer, parent, labels, policy="ALL")
        entry = pit.get(parent)
        assert entry is not None

        port = MockSubstratePort(hold_terminals=True)
        inbound: asyncio.Queue = asyncio.Queue()
        await port.start(inbound)

        # Map leaf index (canonical digest order) → correlation
        corrs: dict[str, bytes] = {}
        for label in labels:
            corr = hashlib.sha256(f"corr-{label}".encode()).digest()
            corrs[label] = corr
            port.script_response(corr, responses[label])
            await port.send_request(
                Name(("cap", label)),
                b"req",
                correlation=corr,
                deadline=1000.0,
            )

        await port.release_terminals([corrs[label] for label in order])

        # Drain ResponseArrived and place digests into the PIT by leaf index
        index_by_digest = {
            dig: i for i, dig in enumerate(entry.expected_subintent_set)
        }
        label_by_corr = {corr: label for label, corr in corrs.items()}
        placed = 0
        while placed < len(labels):
            event = await inbound.get()
            if not isinstance(event, ResponseArrived):
                continue
            label = label_by_corr[event.correlation]
            leaf_index = index_by_digest[_digest(label)]
            pit.record_response(
                parent,
                leaf_index,
                hashlib.sha256(event.payload).digest(),
            )
            placed += 1

        completed = maybe_complete(pit, parent, now_ms=99)
        assert completed is not None
        await port.stop()
        return completed.trace_root

    root_fwd = await run(["a", "b", "c"])
    root_rev = await run(["c", "b", "a"])
    root_mix = await run(["b", "a", "c"])
    assert root_fwd == root_rev == root_mix
    # Independent verifier agrees
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x11" * 32
    _commit(pit, issuer, parent, labels)
    entry = pit.get(parent)
    assert entry is not None
    for i, label in enumerate(
        sorted(labels, key=lambda lab: _digest(lab))
    ):
        pit.record_response(
            parent, i, hashlib.sha256(responses[label]).digest()
        )
    done = maybe_complete(pit, parent, now_ms=1)
    assert done is not None
    assert done.trace_root == root_fwd
    steers = tuple(leaf.steer_chain_head for leaf in done.leaves)
    resps = tuple(leaf.response_digest for leaf in done.leaves)
    assert verify_claimed_root(done.expected_subintent_set, steers, resps, root_fwd)


def test_terminate_root_final_matches_recompute() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x05" * 32
    _commit(pit, issuer, parent, ["x", "y"], policy="BEST_EFFORT")
    pit.record_response(parent, 0, _digest("rx"))
    entry = maybe_complete(pit, parent, now_ms=9, deadline_reached=True)
    assert entry is not None
    steers = tuple(leaf.steer_chain_head for leaf in entry.leaves)
    resps = tuple(leaf.response_digest for leaf in entry.leaves)
    assert steers == (NULL_STEER, NULL_STEER)
    assert resps[1] == NULL_RESPONSE
    assert (
        recompute_trace_root(entry.expected_subintent_set, steers, resps)
        == entry.trace_root
    )


def test_quorum_rejects_bad_bounds_and_wrong_n() -> None:
    with pytest.raises(ValueError):
        QuorumPolicy(k=0, n=3)
    with pytest.raises(ValueError):
        QuorumPolicy(k=4, n=3)
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x06" * 32
    _commit(pit, issuer, parent, ["a", "b"], policy="QUORUM(2,3)")
    entry = pit.get(parent)
    assert entry is not None
    policy = parse_aggregation_policy(entry.aggregation_policy)
    assert policy.is_satisfied(entry) is False


def test_maybe_complete_idempotent_and_unknown() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x07" * 32
    _commit(pit, issuer, parent, ["a"], policy="ALL")
    pit.record_response(parent, 0, _digest("ra"))
    first = maybe_complete(pit, parent, now_ms=1)
    second = maybe_complete(pit, parent, now_ms=2)
    assert first is not None and second is first
    with pytest.raises(KeyError):
        maybe_complete(pit, b"\xff" * 32, now_ms=3)


def test_fail_closed_best_effort_and_all_noop() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x08" * 32
    _commit(pit, issuer, parent, ["a"], policy="BEST_EFFORT")
    entry = pit.get(parent)
    assert entry is not None
    fail_closed_if_unsatisfiable(entry)
    fail_closed_if_unsatisfiable(entry, deadline_reached=True)


def test_recompute_length_mismatch() -> None:
    with pytest.raises(ValueError):
        recompute_trace_root((_digest("a"),), (NULL_STEER,), (_digest("r"), _digest("s")))
    assert not verify_claimed_root(
        (_digest("a"),),
        (NULL_STEER,),
        (_digest("r"), _digest("s")),
        b"\x00" * 32,
    )


def test_resolved_count() -> None:
    from agentic.agentic_layer.aggregation import resolved_count

    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\x09" * 32
    _commit(pit, issuer, parent, ["a", "b"])
    entry = pit.get(parent)
    assert entry is not None
    assert resolved_count(entry) == 0
    pit.record_timeout(parent, 0)
    assert resolved_count(entry) == 1

