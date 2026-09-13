# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""NF-9: the forwarder-backed response seam emits a Content downward.

On a port-less AgenticLayer the reply is pushed onto ``queue_to_lower`` as
``[reply_ref, Content(capability_name, payload)]``; with a forwarder-backed
``response_sink`` installed, ``PicnSubstratePort.send_response`` delegates
instead of recording locally.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from PiCN.Packets import Content

from agentic.adapters.picn import PicnSubstratePort
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.binding import DeterministicBackend
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact

INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": True,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": True,
}


def _descriptor():
    key = generate_ed25519_private_key()
    body = {
        "kind": "capability-descriptor",
        "domain": "svc",
        "task": "ping",
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
    return parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)), capability_path=(b"svc", b"ping")
    )


@pytest.mark.asyncio
async def test_transmit_response_builds_content_on_seam() -> None:
    layer = AgenticLayer(runtime="async")
    seam: asyncio.Queue = asyncio.Queue(maxsize=4)
    layer.queue_to_lower = seam
    name = Name((b"cap", b"issuer", b"svc", b"v=1"))
    await layer._transmit_response(reply_ref=17, name=name, payload=b"body")
    item = seam.get_nowait()
    assert item[0] == 17
    assert isinstance(item[1], Content)
    assert tuple(item[1].name.components) == name.components
    assert item[1].get_bytes() == b"body"


@pytest.mark.asyncio
async def test_on_inbound_request_uses_seam_when_portless() -> None:
    desc = _descriptor()
    layer = AgenticLayer(runtime="async")

    async def handler(_payload: dict) -> dict:
        return {"ok": True}

    layer.register_capability(
        desc, DeterministicBackend(handler, input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA)
    )
    seam: asyncio.Queue = asyncio.Queue(maxsize=4)
    layer.queue_to_lower = seam
    corr = b"\x33" * 32
    layer._inbound_reply_ref[corr] = 8
    from agentic.port.events import InboundRequest

    await layer._on_inbound_request(
        InboundRequest(correlation=corr, name=desc.name, payload=b"{}", at=0.0)
    )
    item = seam.get_nowait()
    assert item[0] == 8
    body = json.loads(item[1].get_bytes().decode())
    assert body["output_valid"] is True


@pytest.mark.asyncio
async def test_forwarder_backed_sink_delegates() -> None:
    sink_calls: list[tuple[bytes, bytes]] = []

    async def sink(correlation: bytes, payload: bytes) -> None:
        sink_calls.append((correlation, payload))

    port = PicnSubstratePort("127.0.0.1", 9, log_level=255, response_sink=sink)
    await port.send_response(b"\x01" * 32, b"reply")
    assert sink_calls == [(b"\x01" * 32, b"reply")]
    assert port._response_payloads == {}


@pytest.mark.asyncio
async def test_without_sink_records_locally() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    await port.send_response(b"\x02" * 32, b"reply")
    assert port._response_payloads[b"\x02" * 32] == b"reply"


def test_issuer_der_is_bytes() -> None:
    key = generate_ed25519_private_key()
    der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    assert isinstance(der, bytes)