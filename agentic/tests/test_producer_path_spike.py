# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""PR-1 spike gate: the producer path and response seam on the real NFN stack.

Three assertions, all RED on pre-fix code and GREEN after the NF-6/NF-7/NF-9
fixes (design v5 §3.1, strengthened by v5.1 §3.1(c)):

(a) an Interest driven through the forwarder's layer stack reaches
    ``AgenticLayer.data_from_lower`` and is translated to an ``InboundRequest``
    dispatched to ``_on_inbound_request`` with the face id preserved as the
    reply ref;
(b) a Content injected from higher traverses NFN's ``handle_from_higher`` (the
    NF-6 pass-through) and reaches ``ICNLayerCore.handle_content`` emitting
    ``Outbound("lower", [requester_face, content])`` — RED pre-NF-6;
(c) on a PORT-LESS forwarder the response seam runs: ``hub.invoke`` fires AND
    the response Content is pushed onto ``AgenticLayer.queue_to_lower`` — RED
    pre-NF-9 (``layer.py`` asserted a port before ``hub.invoke``).

Run: ``python -m pytest agentic/tests/test_producer_path_spike.py -v``
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from PiCN.Layers.LinkLayer.Interfaces import SimulationBus
from PiCN.Layers.PacketEncodingLayer.Encoder import NdnTlvEncoder
from PiCN.Packets import Content, Interest, Name as PicnName
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime
from PiCN.Processes.Outbound import Outbound

from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.binding import DeterministicBackend
from agentic.port.events import InboundRequest
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact

from demo.bus_topology import unblock_sim_interfaces

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
        verify_artefact(sign_artefact(body, key)), capability_path=(b"hospital", b"beds")
    )
    return desc, key


def _issuer_der(key: Any) -> bytes:
    return key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


async def _drain_until(predicate, *, tries: int = 200, delay: float = 0.005) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(delay)
    return predicate()


@pytest.mark.asyncio
async def test_a_inbound_translation() -> None:
    """(a) Interest driven through the stack reaches data_from_lower + dispatch."""
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder)
    fwd = AgenticForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface("edge-a")],
        log_level=255,
        runtime=Runtime.ASYNC,
    )
    desc, _key = _descriptor()
    layer = fwd.agentic

    dispatched: list[InboundRequest] = []
    original = layer._on_inbound_request

    async def spy(event: InboundRequest) -> None:
        dispatched.append(event)
        await original(event)

    layer._on_inbound_request = spy  # type: ignore[method-assign]
    await fwd.start_forwarder_async()
    try:
        face_id = 7
        wire_name = PicnName(list(desc.name.components))
        # Drive a decoded Interest at the ICN's lower interface: the real
        # stack then takes it upward through NFN's interest pass-through.
        await fwd.icnlayer.queue_from_lower.put([face_id, Interest(wire_name)])

        ok = await _drain_until(lambda: len(dispatched) == 1)
        assert ok, "Interest did not reach _on_inbound_request via the stack"
        event = dispatched[0]
        assert event.name.components == desc.name.components
        expected_corr = hashlib.sha256(
            b"inbound:" + b"/".join(desc.name.components)
        ).digest()
        assert event.correlation == expected_corr
        # Reply ref preserved for the response seam (face id at [0]).
        assert layer._inbound_reply_ref.get(event.correlation) == face_id
    finally:
        unblock_sim_interfaces(fwd)
        await fwd.stop_forwarder_async()
        for iface in fwd.interfaces:
            iface.close()


@pytest.mark.asyncio
async def test_b_nfn_passthrough() -> None:
    """(b) Injected Content traverses NFN handle_from_higher → handle_content."""
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder)
    fwd = AgenticForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface("edge-b")],
        log_level=255,
        runtime=Runtime.ASYNC,
    )
    captured: list[list[Outbound]] = []
    core = fwd.icnlayer._core
    original_handle_content = core.handle_content

    def spy_handle_content(face_id: int, content: Content, **kwargs: Any) -> list[Outbound]:
        out = original_handle_content(face_id, content, **kwargs)
        captured.append(out)
        return out

    core.handle_content = spy_handle_content  # type: ignore[method-assign]
    await fwd.start_forwarder_async()
    try:
        face_id = 11
        # Non-NFN, non-R2C name — must pass through NFN downward.
        name = PicnName("/cap/responder/result/v=1")
        # First drive an Interest to install the inbound PIT entry (local_app=False)
        # so handle_content resolves to Outbound("lower", [requester_face, ...]).
        await fwd.icnlayer.queue_from_lower.put([face_id, Interest(name)])
        entry = await _drain_until(
            lambda: fwd.pit.find_pit_entry(name) is not None
        )
        assert entry, "inbound PIT entry was not installed"

        content = Content(name, b"payload")
        await fwd.agentic.queue_to_lower.put([face_id, content])

        ok = await _drain_until(lambda: len(captured) == 1)
        assert ok, "Content did not reach ICNLayerCore.handle_content through NFN"
        outs = captured[0]
        assert len(outs) == 1
        out = outs[0]
        assert out.direction == "lower"
        reply_face, reply_packet = out.item
        assert reply_face == face_id
        assert isinstance(reply_packet, Content)
        assert reply_packet.name.components == name.components
    finally:
        unblock_sim_interfaces(fwd)
        await fwd.stop_forwarder_async()
        for iface in fwd.interfaces:
            iface.close()


@pytest.mark.asyncio
async def test_c_portless_response_seam() -> None:
    """(c) Port-less forwarder: hub.invoke fires AND reply hits queue_to_lower."""
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder)
    # No port_adapter — exactly the D/E edge configuration.
    fwd = AgenticForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface("edge-c")],
        log_level=255,
        runtime=Runtime.ASYNC,
    )
    assert fwd.agentic.port is None

    desc, _key = _descriptor()
    invocations: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        invocations.append(payload)
        return {"beds_free": 4}

    fwd.register_capability(
        desc,
        DeterministicBackend(
            handler, input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA
        ),
        backend_label="deterministic",
    )

    layer = fwd.agentic
    hub_invokes: list[Any] = []
    original_invoke = layer._hub.invoke

    async def spy_invoke(*args: Any, **kwargs: Any) -> Any:
        hub_invokes.append(args)
        return await original_invoke(*args, **kwargs)

    layer._hub.invoke = spy_invoke  # type: ignore[method-assign]

    await fwd.start_forwarder_async()
    try:
        real_seam = layer.queue_to_lower
        assert real_seam is not None
        seam_captured: list[Any] = []

        class _SeamSpy:
            async def put(self, item: Any) -> None:
                seam_captured.append(item)
                await real_seam.put(item)

        layer.queue_to_lower = _SeamSpy()  # type: ignore[assignment]

        face_id = 3
        wire_name = PicnName(list(desc.name.components))
        await fwd.icnlayer.queue_from_lower.put([face_id, Interest(wire_name)])

        ok = await _drain_until(lambda: len(seam_captured) == 1)
        assert hub_invokes, "hub.invoke spy did not fire"
        assert ok, "response Content was not emitted onto AgenticLayer.queue_to_lower"

        item = seam_captured[0]
        assert isinstance(item, list)
        assert item[0] == face_id
        assert isinstance(item[1], Content)
        assert item[1].name.components == list(desc.name.components)
        body = json.loads(item[1].get_bytes().decode("utf-8"))
        assert "output_valid" in body
        assert body["input_rejected"] is True
        assert body["payload"] is None
    finally:
        unblock_sim_interfaces(fwd)
        await fwd.stop_forwarder_async()
        for iface in fwd.interfaces:
            iface.close()