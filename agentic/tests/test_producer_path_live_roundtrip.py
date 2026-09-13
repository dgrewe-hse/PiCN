# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""PR-1 live roundtrip: ambulance submit_intent → edge producer → Content back.

SimulationBus, real ``AgenticForwarder`` edge with a **registered**
``DeterministicBackend`` (the cardiac demo answered from CS and never reached
``hub.invoke``). Proves the NF-6/NF-7/NF-9 fixes end-to-end: the Interest
reaches the producer, ``hub.invoke`` fires, the Content returns through the
edge's ICN PIT, and the ambulance's Context PIT trace root is final.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import time
from typing import Any

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from PiCN.Layers.LinkLayer.Interfaces import SimulationBus
from PiCN.Layers.PacketEncodingLayer.Encoder import NdnTlvEncoder
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

from agentic.adapters.picn import PicnSubstratePort
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.decomposer import SubIntent
from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.binding import DeterministicBackend
from agentic.port.events import ResponseArrived, SubstrateEvent
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact

from demo.bus_topology import unblock_sim_interfaces

INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": True,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"beds_free": {"type": "integer"}},
    "required": ["beds_free"],
    "additionalProperties": True,
}


def _descriptor_with_name(name: Name):
    key = generate_ed25519_private_key()
    body = {
        "kind": "capability-descriptor",
        "domain": "hospital",
        "task": "beds",
        "version": "1.0.0",
        "constraints": {},
        "attestation_policy": "optional",
        "reputation_threshold": "0.5",
        "cost": "1",
        "input_schema": INPUT_SCHEMA,
        "output_schema": OUTPUT_SCHEMA,
        "freshness_bound_s": 60,
        "revocation_pointer": "none",
    }
    desc = parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)), capability_path=(b"hospital", b"beds")
    )
    # The wire name submit_intent emits is `/cap/fwd/<capability>`; register
    # the producer under exactly that name.
    return dataclasses.replace(desc, name=name)


class _RecordingPort(PicnSubstratePort):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.responses: dict[bytes, bytes] = {}

    async def _put(self, event: SubstrateEvent) -> None:
        if isinstance(event, ResponseArrived):
            self.responses[event.correlation] = event.payload
        await super()._put(event)


@pytest.mark.asyncio
async def test_producer_path_live_roundtrip() -> None:
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder)
    edge = AgenticForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface("edge")],
        log_level=255,
        runtime=Runtime.ASYNC,
    )
    wire_name = Name((b"cap", b"fwd", b"hospital", b"beds"))
    desc = _descriptor_with_name(wire_name)
    invocations: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        invocations.append(payload)
        return {"beds_free": 4}

    edge.register_capability(
        desc,
        DeterministicBackend(
            handler, input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA
        ),
        backend_label="deterministic",
    )

    port = _RecordingPort("edge", None, encoder=encoder, interfaces=[bus.add_interface("ambulance")], log_level=255)
    layer = AgenticLayer(runtime="async", port=port)

    await edge.start_forwarder_async()
    await layer.start_port()
    bus.start_process()
    try:
        key = generate_ed25519_private_key()
        issuer_der = key.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        parent = hashlib.sha256(b"live-intent").digest()
        subs = [SubIntent(index=0, capability=("hospital", "beds"), bindings={})]

        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=issuer_der,
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=time.monotonic() + 5.0,
                now_ms=0,
            ),
            timeout=8.0,
        )
        entry = layer.pit.get(parent)
        assert entry is not None and entry.terminated
        assert root == entry.trace_root
        assert invocations, "edge hub.invoke backend was never invoked"
        assert len(port.responses) == 1
    finally:
        unblock_sim_interfaces(edge, port)
        await layer.stop_port()
        await edge.stop_forwarder_async()
        for iface in edge.interfaces:
            iface.close()
        bus.stop_process()