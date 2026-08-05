# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Context PIT capacity: refuse rather than evict in-flight state."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.agentic_layer.context_pit import ContextPIT
from agentic.port.errors import CapacityExhausted
from agentic.trust import generate_ed25519_private_key


def _issuer_der(key) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _fill_inflight(pit: ContextPIT, issuer, n: int, *, now_ms: int = 0) -> list[bytes]:
    parents: list[bytes] = []
    for i in range(n):
        parent = i.to_bytes(32, "big")
        parents.append(parent)
        pit.commit(
            parent_intent_digest=parent,
            issuer_public_key_der=_issuer_der(issuer),
            aggregation_policy="ALL",
            leaf_specs=[(b"\x01" * 32, b"p", 1, ("c",), b"k")],
            now_ms=now_ms,
        )
    return parents


def test_capacity_refuses_without_touching_inflight() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT(max_entries=3)
    parents = _fill_inflight(pit, issuer, 3)
    expected_sets = {
        p: pit.get(p).expected_subintent_set  # type: ignore[union-attr]
        for p in parents
    }
    with pytest.raises(CapacityExhausted):
        pit.commit(
            parent_intent_digest=b"\xff" * 32,
            issuer_public_key_der=_issuer_der(issuer),
            aggregation_policy="ALL",
            leaf_specs=[(b"\x02" * 32, b"p", 1, ("c",), b"k")],
            now_ms=0,
        )
    assert pit.context_pit_refusals == 1
    assert pit.context_pit_entries == 3
    for p in parents:
        entry = pit.get(p)
        assert entry is not None
        assert entry.expected_subintent_set == expected_sets[p]
        assert entry.terminated is False


def test_evicts_oldest_terminated_under_pressure() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT(max_entries=2, result_retention_ms=10_000)
    p0, p1 = _fill_inflight(pit, issuer, 2)
    pit.terminate(p0, now_ms=100)
    pit.terminate(p1, now_ms=200)
    # At capacity with only terminated entries: new commit evicts oldest (p0).
    p2 = b"\x02" * 32
    pit.commit(
        parent_intent_digest=p2,
        issuer_public_key_der=_issuer_der(issuer),
        aggregation_policy="ALL",
        leaf_specs=[(b"\x03" * 32, b"p", 1, ("c",), b"k")],
        now_ms=250,
    )
    assert pit.get(p0) is None
    assert pit.get(p1) is not None
    assert pit.get(p2) is not None
    assert pit.context_pit_refusals == 0


def test_reap_expired_terminated_before_refuse() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT(max_entries=1, result_retention_ms=50)
    p0 = _fill_inflight(pit, issuer, 1)[0]
    pit.terminate(p0, now_ms=0)
    # Past retention: reap frees the slot.
    pit.commit(
        parent_intent_digest=b"\x09" * 32,
        issuer_public_key_der=_issuer_der(issuer),
        aggregation_policy="ALL",
        leaf_specs=[(b"\x04" * 32, b"p", 1, ("c",), b"k")],
        now_ms=50,
    )
    assert pit.get(p0) is None
    assert pit.context_pit_entries == 1
