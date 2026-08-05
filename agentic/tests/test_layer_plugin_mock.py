# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""G.1: plug-in API and AgenticLayer wiring against MockSubstratePort."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.adapters.mock import MockSubstratePort
from agentic.adapters.mock.clock import ManualClock
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.agentic_layer.decomposer import SubIntent
from agentic.binding import DeterministicBackend
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact
from agentic.trust.merkle import NULL_RESPONSE


INPUT_SCHEMA = {
    "type": "object",
    "properties": {"patient_id": {"type": "string"}},
    "required": ["patient_id"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"beds_free": {"type": "integer"}},
    "required": ["beds_free"],
    "additionalProperties": False,
}


def _descriptor():
    key = generate_ed25519_private_key()
    path = (b"hospital", b"beds")
    body = {
        "kind": "capability-descriptor",
        "domain": "hospital",
        "task": "beds",
        "version": "1.0.0",
        "constraints": {"jurisdiction": "DE"},
        "attestation_policy": "optional",
        "reputation_threshold": "0.5",
        "cost": "1",
        "input_schema": INPUT_SCHEMA,
        "output_schema": OUTPUT_SCHEMA,
        "freshness_bound_s": 60,
        "revocation_pointer": "none",
    }
    desc = parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)), capability_path=path
    )
    return desc, key


@pytest.mark.asyncio
async def test_register_and_serve_inbound_on_mock_port() -> None:
    """Producer plug-in: inbound capability request → backend → send_response."""
    desc, _key = _descriptor()
    clock = ManualClock(1.0)
    port = MockSubstratePort(clock=clock)
    layer = AgenticLayer(runtime="async", port=port, quote_id_provider=lambda: "q-mock")

    async def handler(payload: dict) -> dict:
        assert payload["patient_id"] == "p-1"
        return {"beds_free": 4}

    layer.register_capability(
        desc,
        DeterministicBackend(handler, input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA),
        backend_label="deterministic",
    )
    await layer.start_port()
    try:
        corr = b"\x11" * 32
        await port.inject_inbound_request(
            desc.name,
            json.dumps({"patient_id": "p-1"}).encode(),
            correlation=corr,
        )
        await asyncio.wait_for(asyncio.sleep(0), timeout=1.0)
        # Allow the event loop task to drain.
        for _ in range(20):
            if corr in port.responses_sent:
                break
            await asyncio.sleep(0.01)
        assert corr in port.responses_sent
        body = json.loads(port.responses_sent[corr].decode())
        assert body["output_valid"] is True
        assert body["payload"] == {"beds_free": 4}
        assert body["quote_id"] == "q-mock"
    finally:
        await layer.stop_port()


@pytest.mark.asyncio
async def test_submit_intent_commits_before_forward_and_aggregates() -> None:
    """I2: PIT commit before send_request; ALL policy yields a final root."""
    clock = ManualClock(0.0)
    port = MockSubstratePort(clock=clock)
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        issuer = generate_ed25519_private_key()
        issuer_der = issuer.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        parent = hashlib.sha256(b"intent-1").digest()
        subs = [
            SubIntent(index=0, capability=("hospital", "beds"), bindings={}),
            SubIntent(index=1, capability=("traffic", "eta"), bindings={}),
        ]
        # Script responses for the correlations the layer will use.
        for sub in subs:
            digest = hashlib.sha256(
                parent
                + bytes(str(sub.index), "ascii")
                + b"/".join(c.encode() for c in sub.capability)
            ).digest()
            corr = hashlib.sha256(parent + digest).digest()
            port.script_response(corr, f"ok-{sub.index}".encode())

        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=issuer_der,
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
            ),
            timeout=2.0,
        )
        entry = layer.pit.get(parent)
        assert entry is not None and entry.terminated
        assert entry.committed is True
        assert all(leaf.forwarded for leaf in entry.leaves)
        assert root == entry.trace_root
        assert all(leaf.response_digest != NULL_RESPONSE for leaf in entry.leaves)
    finally:
        await layer.stop_port()


@pytest.mark.asyncio
async def test_timeout_leaf_becomes_explicit_null() -> None:
    """I3: timed-out sub-intent becomes NULL, never pruned."""
    clock = ManualClock(0.0)
    port = MockSubstratePort(clock=clock)
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        issuer = generate_ed25519_private_key()
        issuer_der = issuer.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        parent = hashlib.sha256(b"intent-timeout").digest()
        subs = [SubIntent(index=0, capability=("x",), bindings={})]
        digest = hashlib.sha256(parent + b"0" + b"x").digest()
        corr = hashlib.sha256(parent + digest).digest()
        port.timeout_on(corr)

        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=issuer_der,
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
            ),
            timeout=2.0,
        )
        entry = layer.pit.get(parent)
        assert entry is not None
        assert len(entry.leaves) == 1
        assert entry.leaves[0].response_digest == NULL_RESPONSE
        assert root == entry.trace_root
    finally:
        await layer.stop_port()
