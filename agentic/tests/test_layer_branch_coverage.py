# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution under the BSD 3-Clause License; see the LICENSE file for text.

"""Targeted branch coverage for the PR-1 changes in AgenticLayer."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from PiCN.Packets import Content, Interest, Name as PicnName
from PiCN.Processes.Outbound import Outbound

from agentic.adapters.mock import MockSubstratePort
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.decomposer import SubIntent
from agentic.port.events import (
    InboundRequest,
    RequestFailed,
    RequestSent,
    RequestTimedOut,
    ResponseArrived,
)
from agentic.port.errors import Unreachable
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key
from agentic.trust.merkle import NULL_RESPONSE


def _issuer_der() -> bytes:
    key = generate_ed25519_private_key()
    return key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


def test_hub_and_cfib_properties() -> None:
    layer = AgenticLayer(runtime="async")
    assert layer.hub is not None
    assert layer.cfib is not None
    assert layer.pit is not None
    assert layer.port is None


def test_attach_port_after_start_raises() -> None:
    from agentic.adapters.mock import MockSubstratePort as _P

    layer = AgenticLayer(runtime="async", port=_P())
    layer._started_port = True
    with pytest.raises(RuntimeError):
        layer.attach_port(_P())


def test_set_decomposer() -> None:
    layer = AgenticLayer(runtime="async")
    from agentic.agentic_layer.decomposer import BoundedDecomposer

    dec = BoundedDecomposer(templates=[], f_max=1, d_max=1)
    layer.set_decomposer(dec)
    assert layer._decomposer is dec


@pytest.mark.asyncio
async def test_start_port_requires_port() -> None:
    layer = AgenticLayer(runtime="async")
    with pytest.raises(RuntimeError):
        await layer.start_port()


@pytest.mark.asyncio
async def test_stop_port_idempotent_when_never_started() -> None:
    layer = AgenticLayer(runtime="async")
    await layer.stop_port()  # no-op, must not raise


@pytest.mark.asyncio
async def test_dispatch_ignores_request_sent() -> None:
    layer = AgenticLayer(runtime="async")
    await layer._dispatch_event(
        RequestSent(correlation=b"c", name=Name((b"x",)), at=0.0)
    )


@pytest.mark.asyncio
async def test_on_timeout_unknown_correlation() -> None:
    layer = AgenticLayer(runtime="async")
    await layer._on_timeout(
        RequestTimedOut(correlation=b"nope", name=Name((b"x",)), at=0.0)
    )


@pytest.mark.asyncio
async def test_on_failed_unknown_correlation() -> None:
    layer = AgenticLayer(runtime="async")
    await layer._on_failed(
        RequestFailed(
            correlation=b"nope",
            name=Name((b"x",)),
            reason=Unreachable(),
            at=0.0,
        )
    )


@pytest.mark.asyncio
async def test_data_from_lower_accepts_outbound_wrapper() -> None:
    layer = AgenticLayer(runtime="async")
    async def spy(_event):  # type: ignore[no-untyped-def]
        return None

    layer._dispatch_event = spy  # type: ignore[method-assign]
    wrapped = Outbound("lower", [4, Interest(PicnName("/cap/x/v=1"))])
    await layer.data_from_lower(None, None, wrapped)


@pytest.mark.asyncio
async def test_transmit_response_no_queue() -> None:
    layer = AgenticLayer(runtime="async")
    await layer._transmit_response(reply_ref=1, name=Name((b"x",)), payload=b"p")


@pytest.mark.asyncio
async def test_on_inbound_request_no_port_no_reply_ref_drops_after_invoke() -> None:
    layer = AgenticLayer(runtime="async")
    from agentic.agentic_layer.descriptor import parse_descriptor_body
    from agentic.trust import sign_artefact, verify_artefact

    body = {
        "kind": "capability-descriptor",
        "domain": "svc",
        "task": "t",
        "version": "1.0.0",
        "constraints": {},
        "attestation_policy": "optional",
        "reputation_threshold": "0.5",
        "cost": "1",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "freshness_bound_s": 5,
        "revocation_pointer": "none",
    }
    key = generate_ed25519_private_key()
    desc = parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)), capability_path=(b"svc", b"t")
    )
    from agentic.binding import DeterministicBackend

    layer.register_capability(
        desc,
        DeterministicBackend(
            lambda p: {"ok": True}, input_schema={"type": "object"}, output_schema={"type": "object"}
        ),
    )
    # No port, no reply ref → early drop after name/registry checks.
    await layer._on_inbound_request(
        InboundRequest(correlation=b"\x01" * 32, name=desc.name, payload=b"{}", at=0.0)
    )


@pytest.mark.asyncio
async def test_on_response_unknown_and_terminated() -> None:
    layer = AgenticLayer(runtime="async")
    await layer._on_response(
        ResponseArrived(correlation=b"\x02" * 32, name=Name((b"x",)), payload=b"p", at=0.0)
    )


@pytest.mark.asyncio
async def test_record_leaf_failure_idempotent() -> None:
    layer = AgenticLayer(runtime="async")
    parent = hashlib.sha256(b"idem").digest()
    entry = layer._pit.commit(
        parent_intent_digest=parent,
        issuer_public_key_der=_issuer_der(),
        aggregation_policy="ALL",
        leaf_specs=[(hashlib.sha256(b"l0").digest(), b"", None, ("x",), b"")],
        now_ms=0,
    )
    layer._leaf_index[b"corr"] = (parent, 0)
    await layer._record_leaf_failure(parent, 0, now_ms=0)
    assert entry.leaves[0].response_digest == NULL_RESPONSE
    # Second call after termination is a no-op.
    await layer._record_leaf_failure(parent, 0, now_ms=0)
    # Unknown parent is a no-op.
    await layer._record_leaf_failure(b"\xff" * 32, 0, now_ms=0)


@pytest.mark.asyncio
async def test_submit_intent_unknown_dispatch() -> None:
    port = MockSubstratePort()
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        with pytest.raises(ValueError):
            await layer.submit_intent(
                parent_intent_digest=hashlib.sha256(b"d").digest(),
                issuer_public_key_der=_issuer_der(),
                sub_intents=[SubIntent(index=0, capability=("x",), bindings={})],
                deadline=10.0,
                dispatch="nope",
            )
    finally:
        await layer.stop_port()


@pytest.mark.asyncio
async def test_send_response_port_path_still_used() -> None:
    """Port-attached: reply goes to port.send_response, not the seam."""
    from agentic.agentic_layer.descriptor import parse_descriptor_body
    from agentic.binding import DeterministicBackend
    from agentic.trust import sign_artefact, verify_artefact

    body = {
        "kind": "capability-descriptor",
        "domain": "svc",
        "task": "t",
        "version": "1.0.0",
        "constraints": {},
        "attestation_policy": "optional",
        "reputation_threshold": "0.5",
        "cost": "1",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "freshness_bound_s": 5,
        "revocation_pointer": "none",
    }
    key = generate_ed25519_private_key()
    desc = parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)), capability_path=(b"svc", b"t")
    )
    port = MockSubstratePort()
    layer = AgenticLayer(runtime="async", port=port)
    layer.register_capability(
        desc,
        DeterministicBackend(
            lambda p: {"ok": True},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
    )
    await layer.start_port()
    try:
        corr = b"\x05" * 32
        await port.inject_inbound_request(desc.name, b"{}", correlation=corr)
        for _ in range(50):
            if corr in port.responses_sent:
                break
            await asyncio.sleep(0.01)
        assert corr in port.responses_sent
        assert layer._inbound_reply_ref == {}
    finally:
        await layer.stop_port()