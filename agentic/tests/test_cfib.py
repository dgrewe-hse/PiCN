# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests for the capability FIB two-stage match."""

from __future__ import annotations

import asyncio

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.adapters.mock import MockSubstratePort
from agentic.agentic_layer.cfib import (
    CapabilityFIB,
    MatchConstraints,
    OptimisticReputation,
)
from agentic.agentic_layer.descriptor import CapabilityDescriptor, register_descriptor
from agentic.agentic_layer.naming import capability_lpm_prefix, capability_name
from agentic.port.names import EndpointRef
from agentic.trust import generate_ed25519_private_key, sign_artefact


def _pub(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _add(
    fib: CapabilityFIB,
    key: Ed25519PrivateKey,
    *,
    path: tuple[bytes, ...],
    version: str,
    cost: str = "10",
    policy: str = "required",
    jurisdiction: str = "DE-BW",
    latency_bound_ms: int = 100,
    reputation_threshold: str = "0.10",
) -> CapabilityDescriptor:
    body: dict[str, object] = {
        "kind": "capability-descriptor",
        "domain": "hospital-capacity",
        "task": "bed-availability-query",
        "version": version,
        "constraints": {
            "jurisdiction": jurisdiction,
            "latency_bound_ms": latency_bound_ms,
        },
        "attestation_policy": policy,
        "reputation_threshold": reputation_threshold,
        "cost": cost,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "freshness_bound_s": 3600,
        "revocation_pointer": "/revocation/x",
    }
    desc = register_descriptor(sign_artefact(body, key), capability_path=path)
    fib.register(desc)
    return desc


def test_stage1_lpm_and_stage2_cost_filter() -> None:
    fib = CapabilityFIB()
    key = generate_ed25519_private_key()
    cheap = _add(fib, key, path=(b"hospital", b"beds"), version="1.0.0", cost="10")
    pricey = _add(fib, key, path=(b"hospital", b"beds"), version="2.0.0", cost="50")
    _add(fib, key, path=(b"hospital",), version="1.0.0", cost="1")

    query = capability_name(_pub(key), (b"hospital", b"beds"), "9.9.9")
    matched = fib.match(query, MatchConstraints(max_cost="20", jurisdiction="DE-BW"))
    assert cheap in matched
    assert pricey not in matched
    # Longer prefix (hospital/beds) beat the shorter hospital-only entry.
    assert all(b"beds" in d.name.components for d in matched)


def test_semver_and_attestation_predicates() -> None:
    fib = CapabilityFIB()
    key = generate_ed25519_private_key()
    old = _add(fib, key, path=(b"svc",), version="1.0.0", policy="required")
    new = _add(fib, key, path=(b"svc",), version="2.1.0", policy="optional")
    query = capability_name(_pub(key), (b"svc",), "0.0.1")

    assert fib.match(query, MatchConstraints(min_version="2.0.0", require_attestation=True)) == ()
    matched = fib.match(query, MatchConstraints(min_version="2.0.0"))
    assert new in matched and old not in matched


class _LowRep(OptimisticReputation):
    def score(self, identity: bytes, *, now: float) -> float:
        return 0.0


def test_reputation_seam_filters_below_threshold() -> None:
    fib = CapabilityFIB(reputation=_LowRep())
    key = generate_ed25519_private_key()
    _add(
        fib,
        key,
        path=(b"svc",),
        version="1.0.0",
        reputation_threshold="0.50",
        policy="none",
    )
    query = capability_name(_pub(key), (b"svc",), "1.0.0")
    assert fib.match(query) == ()


def test_cfib_with_mock_adapter_prefix_table() -> None:
    fib = CapabilityFIB()
    key = generate_ed25519_private_key()
    desc = _add(fib, key, path=(b"hospital", b"beds"), version="1.0.0")
    mock = MockSubstratePort()
    lpm = capability_lpm_prefix(desc.name)

    async def _setup() -> None:
        await mock.start(asyncio.Queue())
        await mock.register_prefix(lpm, EndpointRef(desc.public_key_der))

    asyncio.run(_setup())
    found = mock.lookup(lpm)
    assert found and found[0].value == desc.public_key_der
    query = capability_name(_pub(key), (b"hospital", b"beds"), "1.0.0")
    assert desc in fib.match(query, MatchConstraints(jurisdiction="DE-BW"))
