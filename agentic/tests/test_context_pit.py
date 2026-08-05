# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests for Context PIT commit ordering and steer/Merkle wiring."""

from __future__ import annotations

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.agentic_layer.context_pit import ContextPIT
from agentic.agentic_layer.steer import apply_steer, build_steer_body, sign_steer
from agentic.trust import generate_ed25519_private_key
from agentic.trust.merkle import NULL_RESPONSE, NULL_STEER, PENDING, leaf_digest


def _issuer_der(key) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _commit(pit: ContextPIT, issuer, parent: bytes, digests: list[bytes], *, now_ms: int = 0):
    specs = [
        (d, b"payload-" + d[:4], 1000, ("cap",), b"cred")
        for d in digests
    ]
    return pit.commit(
        parent_intent_digest=parent,
        issuer_public_key_der=_issuer_der(issuer),
        aggregation_policy="ALL",
        leaf_specs=specs,
        now_ms=now_ms,
    )


def test_commit_orders_leaves_canonically_and_inits_pending() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    # Digests deliberately reverse-sorted on input
    d0, d1 = b"\x02" * 32, b"\x01" * 32
    entry = _commit(pit, issuer, b"\xaa" * 32, [d0, d1])
    assert entry.expected_subintent_set == (d1, d0)
    assert all(leaf.response_digest == PENDING for leaf in entry.leaves)
    assert entry.tree.get_leaf(0) == leaf_digest(d1, NULL_STEER, PENDING)
    assert entry.committed
    assert pit.context_pit_entries == 1
    assert pit.context_pit_refusals == 0


def test_workflow_commit_before_forward_ordering() -> None:
    """Invariant I2: expected set committed before any forward (sequence, not state)."""
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    events: list[str] = []

    parent = b"\xbb" * 32
    digests = [b"\x01" * 32, b"\x02" * 32]

    def commit_step() -> None:
        events.append("commit")
        _commit(pit, issuer, parent, digests)

    def forward_step() -> None:
        events.append("forward")
        for i in range(2):
            pit.mark_forwarded(parent, i)

    commit_step()
    forward_step()
    assert events == ["commit", "forward"]
    entry = pit.get(parent)
    assert entry is not None
    assert all(leaf.forwarded for leaf in entry.leaves)
    assert entry.committed


def test_timeout_records_null_never_prunes() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\xcc" * 32
    entry = _commit(pit, issuer, parent, [b"\x01" * 32, b"\x02" * 32])
    pit.record_timeout(parent, 0)
    assert len(entry.leaves) == 2
    assert entry.leaves[0].response_digest == NULL_RESPONSE
    assert entry.leaves[1].response_digest == PENDING
    assert entry.tree.size == 2


def test_terminate_forces_pending_to_null_before_flag() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\xdd" * 32
    entry = _commit(pit, issuer, parent, [b"\x01" * 32, b"\x02" * 32])
    pit.record_response(parent, 0, b"\xab" * 32)

    seen_pending_while_unterminated = False

    # Instrument: check mid-terminate by wrapping set_leaf path via observing leaves
    original_set = entry.tree.set_leaf

    def wrapped(index: int, value: bytes) -> bytes:
        nonlocal seen_pending_while_unterminated
        if not entry.terminated and entry.leaves[index].response_digest == NULL_RESPONSE:
            # After leaf write, terminated must still be False
            assert entry.terminated is False
            seen_pending_while_unterminated = True
        return original_set(index, value)

    entry.tree.set_leaf = wrapped  # type: ignore[method-assign]
    pit.terminate(parent, now_ms=10)
    assert entry.terminated is True
    assert all(leaf.response_digest != PENDING for leaf in entry.leaves)
    assert entry.leaves[0].response_digest == b"\xab" * 32
    assert entry.leaves[1].response_digest == NULL_RESPONSE
    assert seen_pending_while_unterminated


def test_steer_updates_merkle_leaf_via_steer_chain() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = b"\xee" * 32
    entry = _commit(pit, issuer, parent, [b"\x01" * 32, b"\x02" * 32])
    root_before = entry.trace_root
    body = build_steer_body(
        parent_intent_digest=parent,
        leaf_index=0,
        mutations={"payload": b"steered"},
        issued_at_ms=1,
    )
    apply_steer(pit, sign_steer(body, issuer))
    assert entry.leaves[0].payload == b"steered"
    assert entry.leaves[0].steer_chain_head != NULL_STEER
    assert entry.trace_root != root_before
    assert entry.tree.get_leaf(0) == entry.leaves[0].merkle_leaf_value()
