# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests for AgenticSteer allow-list mutation and refusal rules."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.agentic_layer.context_pit import ContextPIT
from agentic.agentic_layer.steer import (
    MAX_STEERS_PER_SUBINTENT,
    NULL_STEER,
    SteerRejected,
    apply_steer,
    build_steer_body,
    sign_steer,
)
from agentic.trust import generate_ed25519_private_key


def _issuer_der(key) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _commit(pit: ContextPIT, issuer, *, terminated: bool = False) -> bytes:
    parent = b"\x11" * 32
    pit.commit(
        parent_intent_digest=parent,
        issuer_public_key_der=_issuer_der(issuer),
        aggregation_policy="ALL",
        leaf_specs=[
            (b"\x01" * 32, b"ecg-v1", 1000, ("vehicle", "ecg"), b"cred-0"),
            (b"\x02" * 32, b"other", 500, ("hospital", "beds"), b"cred-1"),
        ],
        now_ms=0,
    )
    if terminated:
        pit.terminate(parent, now_ms=1)
    return parent


def _signed_steer(
    issuer_key,
    *,
    mutations,
    leaf_index: int = 0,
    parent: bytes = b"\x11" * 32,
):
    body = build_steer_body(
        parent_intent_digest=parent,
        leaf_index=leaf_index,
        mutations=mutations,
        issued_at_ms=1,
    )
    return sign_steer(body, issuer_key)


def test_payload_mutation_applied_and_chain_advances() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = _commit(pit, issuer)
    entry = pit.get(parent)
    assert entry is not None
    assert entry.leaves[0].steer_chain_head == NULL_STEER

    apply_steer(pit, _signed_steer(issuer, mutations={"payload": b"ecg-v2"}))
    assert entry.leaves[0].payload == b"ecg-v2"
    assert entry.leaves[0].steer_count == 1
    assert entry.leaves[0].steer_chain_head != NULL_STEER
    assert entry.expected_subintent_set == (b"\x01" * 32, b"\x02" * 32)
    assert entry.aggregation_policy == "ALL"
    assert entry.leaves[0].capability == ("vehicle", "ecg")
    assert entry.leaves[0].credential == b"cred-0"


def test_latency_bound_mutation_applied() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = _commit(pit, issuer)
    entry = pit.get(parent)
    assert entry is not None
    apply_steer(
        pit,
        _signed_steer(issuer, mutations={"constraints": {"latency_bound": 250}}),
    )
    assert entry.leaves[0].latency_bound == 250
    assert entry.leaves[0].payload == b"ecg-v1"


@pytest.mark.parametrize(
    "mutations",
    [
        {"capability_name": "x"},
        {"capability": "x"},
        {"credential": "x"},
        {"aggregation_policy": "BEST_EFFORT"},
        {"expected_subintent_set": []},
        {"name": "x"},
        {"unknown_field": 1},
        {"constraints": {"jurisdiction": "DE"}},
        {"constraints": {"latency_bound": 1, "extra": 2}},
        {},
    ],
)
def test_forbidden_or_unknown_mutations_rejected_at_build(mutations) -> None:
    with pytest.raises(SteerRejected):
        build_steer_body(
            parent_intent_digest=b"\x11" * 32,
            leaf_index=0,
            mutations=mutations,
            issued_at_ms=1,
        )


def test_wholesale_rejection_does_not_partially_apply() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = _commit(pit, issuer)
    entry = pit.get(parent)
    assert entry is not None
    original = entry.leaves[0].payload
    with pytest.raises(SteerRejected):
        build_steer_body(
            parent_intent_digest=parent,
            leaf_index=0,
            mutations={"payload": b"should-not-apply", "credential": b"x"},
            issued_at_ms=1,
        )
    assert entry.leaves[0].payload == original
    assert entry.leaves[0].steer_count == 0


def test_non_issuer_signature_rejected() -> None:
    issuer = generate_ed25519_private_key()
    other = generate_ed25519_private_key()
    pit = ContextPIT()
    _commit(pit, issuer)
    with pytest.raises(SteerRejected, match="original intent issuer"):
        apply_steer(pit, _signed_steer(other, mutations={"payload": b"x"}))


def test_terminated_entry_refused() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    _commit(pit, issuer, terminated=True)
    with pytest.raises(SteerRejected, match="terminated"):
        apply_steer(pit, _signed_steer(issuer, mutations={"payload": b"x"}))


def test_unknown_digest_refused_creates_nothing() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    with pytest.raises(SteerRejected, match="unknown"):
        apply_steer(
            pit,
            _signed_steer(issuer, mutations={"payload": b"x"}, parent=b"\x22" * 32),
        )
    assert pit.get(b"\x22" * 32) is None


def test_max_steers_per_subintent_enforced() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = _commit(pit, issuer)
    entry = pit.get(parent)
    assert entry is not None
    for i in range(MAX_STEERS_PER_SUBINTENT):
        apply_steer(pit, _signed_steer(issuer, mutations={"payload": f"v{i}".encode()}))
    with pytest.raises(SteerRejected, match="MAX_STEERS"):
        apply_steer(pit, _signed_steer(issuer, mutations={"payload": b"overflow"}))
    assert entry.leaves[0].payload == f"v{MAX_STEERS_PER_SUBINTENT - 1}".encode()


def test_tampered_signature_rejected() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    _commit(pit, issuer)
    env = _signed_steer(issuer, mutations={"payload": b"x"})
    env["sig"]["val"] = "AAAA"
    with pytest.raises(SteerRejected, match="signature"):
        apply_steer(pit, env)


@pytest.mark.parametrize(
    "mutations",
    [
        {"payload": 42},
        {"constraints": "not-an-object"},
        {"constraints": {}},
        {"constraints": {"latency_bound": -1}},
        {"constraints": {"latency_bound": True}},
        {"constraints": {"latency_bound": 1.5}},
    ],
)
def test_mutation_value_type_errors(mutations) -> None:
    with pytest.raises(SteerRejected):
        build_steer_body(
            parent_intent_digest=b"\x11" * 32,
            leaf_index=0,
            mutations=mutations,
            issued_at_ms=1,
        )


def test_build_rejects_negative_leaf_index_and_issued_at() -> None:
    with pytest.raises(SteerRejected, match="leaf_index"):
        build_steer_body(
            parent_intent_digest=b"\x11" * 32,
            leaf_index=-1,
            mutations={"payload": b"x"},
            issued_at_ms=1,
        )
    with pytest.raises(SteerRejected, match="issued_at_ms"):
        build_steer_body(
            parent_intent_digest=b"\x11" * 32,
            leaf_index=0,
            mutations={"payload": b"x"},
            issued_at_ms=-1,
        )


def test_str_payload_encoded() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = _commit(pit, issuer)
    entry = pit.get(parent)
    assert entry is not None
    apply_steer(pit, _signed_steer(issuer, mutations={"payload": "utf8-payload"}))
    assert entry.leaves[0].payload == b"utf8-payload"


def test_sign_rejects_wrong_kind_and_floats() -> None:
    issuer = generate_ed25519_private_key()
    with pytest.raises(SteerRejected, match="agentic-steer"):
        sign_steer({"kind": "other"}, issuer)
    body = build_steer_body(
        parent_intent_digest=b"\x11" * 32,
        leaf_index=0,
        mutations={"payload": b"x"},
        issued_at_ms=1,
    )
    body["cost"] = 1.5
    with pytest.raises(SteerRejected):
        sign_steer(body, issuer)


def test_apply_malformed_envelope_and_wrong_kind() -> None:
    pit = ContextPIT()
    with pytest.raises(SteerRejected, match="malformed"):
        apply_steer(pit, {"body": "x", "sig": {}})
    with pytest.raises(SteerRejected, match="not an agentic-steer"):
        apply_steer(pit, {"body": {"kind": "other"}, "sig": {}})


def test_apply_unsupported_alg_and_out_of_range_leaf() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    _commit(pit, issuer)
    env = _signed_steer(issuer, mutations={"payload": b"x"})
    env["sig"]["alg"] = "RSA"
    with pytest.raises(SteerRejected, match="unsupported"):
        apply_steer(pit, env)

    body = build_steer_body(
        parent_intent_digest=b"\x11" * 32,
        leaf_index=99,
        mutations={"payload": b"y"},
        issued_at_ms=1,
    )
    with pytest.raises(SteerRejected, match="out of range"):
        apply_steer(pit, sign_steer(body, issuer))


def test_apply_missing_body_fields() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    _commit(pit, issuer)
    incomplete = {
        "kind": "agentic-steer",
        "leaf_index": 0,
        "mutations": {"payload": "YQ"},
        "issued_at_ms": 1,
    }
    with pytest.raises(SteerRejected, match="missing required"):
        apply_steer(pit, sign_steer(incomplete, issuer))


def test_both_payload_and_latency_in_one_steer() -> None:
    issuer = generate_ed25519_private_key()
    pit = ContextPIT()
    parent = _commit(pit, issuer)
    entry = pit.get(parent)
    assert entry is not None
    apply_steer(
        pit,
        _signed_steer(
            issuer,
            mutations={
                "payload": b"both",
                "constraints": {"latency_bound": 42},
            },
            leaf_index=1,
        ),
    )
    assert entry.leaves[1].payload == b"both"
    assert entry.leaves[1].latency_bound == 42
    assert entry.leaves[0].payload == b"ecg-v1"
