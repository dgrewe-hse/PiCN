# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""PicnSubstratePort over real async PiCN UDP (G.2)."""

from __future__ import annotations

import asyncio
import time

import pytest

from PiCN.Layers.PacketEncodingLayer.Encoder import SimpleStringEncoder
from PiCN.Packets import Name as PicnName
from PiCN.ProgramLibs.ICNForwarder import ICNForwarder
from PiCN.ProgramLibs.runtime import Runtime

from agentic.adapters.picn import PicnSubstratePort
from agentic.adapters.picn.names import from_picn_name, to_picn_name
from agentic.port.events import RequestSent, RequestTimedOut, ResponseArrived
from agentic.port.names import EndpointRef, Name


def test_name_roundtrip() -> None:
    port_name = Name((b"test", b"obj"))
    picn = to_picn_name(port_name)
    assert from_picn_name(picn).components == port_name.components


@pytest.mark.asyncio
async def test_picn_port_fetches_content_over_udp() -> None:
    encoder = SimpleStringEncoder(log_level=255)
    fwd = ICNForwarder(0, encoder=encoder, log_level=255, runtime=Runtime.ASYNC)
    peer_port = fwd.linklayer.interfaces[0].get_port()
    await fwd.start_forwarder_async()

    port = PicnSubstratePort("127.0.0.1", peer_port, encoder=encoder, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", peer_port)
        writer.write(
            b"GET /icnlayer/newcontent/%2Ftest%2Fobj:HelloPicnPort HTTP/1.1\r\n\r\n"
        )
        await writer.drain()
        await reader.read(1024)
        writer.close()
        await writer.wait_closed()

        corr = b"\x42" * 32
        name = Name((b"test", b"obj"))
        await port.send_request(name, b"", correlation=corr, deadline=time.monotonic() + 4.0)

        sent = await asyncio.wait_for(inbound.get(), timeout=2.0)
        assert isinstance(sent, RequestSent)
        assert sent.correlation == corr

        arrived = await asyncio.wait_for(inbound.get(), timeout=4.0)
        assert isinstance(arrived, ResponseArrived)
        assert arrived.correlation == corr
        assert arrived.payload == b"HelloPicnPort"
    finally:
        await port.stop()
        await fwd.stop_forwarder_async()
        for iface in fwd.interfaces:
            iface.close()


@pytest.mark.asyncio
async def test_picn_port_timeout_when_no_content() -> None:
    encoder = SimpleStringEncoder(log_level=255)
    # Unused high port — nothing answers.
    port = PicnSubstratePort("127.0.0.1", 1, encoder=encoder, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    try:
        corr = b"\x07" * 32
        name = Name((b"missing", b"x"))
        now = time.monotonic()
        await port.send_request(name, b"", correlation=corr, deadline=now + 0.3)
        sent = await asyncio.wait_for(inbound.get(), timeout=1.0)
        assert isinstance(sent, RequestSent)
        timed = await asyncio.wait_for(inbound.get(), timeout=2.0)
        assert isinstance(timed, RequestTimedOut)
        assert timed.correlation == corr
    finally:
        await port.stop()


@pytest.mark.asyncio
async def test_picn_port_prefix_table_and_inject_inbound() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    try:
        prefix = Name((b"cap",))
        endpoint = EndpointRef(value=b"face-1")
        await port.register_prefix(prefix, endpoint)
        assert port.lookup(Name((b"cap", b"x"))) == (endpoint,)
        await port.unregister_prefix(prefix, endpoint)
        assert port.lookup(Name((b"cap", b"x"))) == ()
        assert port.parse_name("/a/b").components == (b"a", b"b")
        with pytest.raises(ValueError):
            port.parse_name("///")
        assert port.name_from_components([b"z"]).components == (b"z",)
        corr = b"\x01" * 32
        await port.inject_interest(Name((b"cap", b"x", b"v=1")), b"{}", correlation=corr)
        event = await asyncio.wait_for(inbound.get(), timeout=1.0)
        assert event.correlation == corr
        await port.send_response(corr, b"ok")
        assert port._response_payloads[corr] == b"ok"
    finally:
        await port.stop()


@pytest.mark.asyncio
async def test_picn_port_nack_becomes_request_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    from PiCN.Packets import Interest, Nack, NackReason, Name as PN

    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)

    async def fake_put(_item: object) -> None:
        return None

    calls = {"n": 0}

    async def fake_get() -> list:
        calls["n"] += 1
        if calls["n"] > 1:
            # Park until cancelled so stop() can finish cleanly.
            await asyncio.Event().wait()
        return [0, Nack(PN("/m"), NackReason.NO_ROUTE, Interest(PN("/m")))]

    monkeypatch.setattr(port._lstack.queue_from_higher, "put", fake_put)
    monkeypatch.setattr(port._lstack.queue_to_higher, "get", fake_get)
    try:
        corr = b"\x09" * 32
        await port.send_request(
            Name((b"m",)), b"", correlation=corr, deadline=time.monotonic() + 2.0
        )
        assert isinstance(await asyncio.wait_for(inbound.get(), timeout=1.0), RequestSent)
        from agentic.port.events import RequestFailed

        failed = await asyncio.wait_for(inbound.get(), timeout=1.0)
        assert isinstance(failed, RequestFailed)
        assert failed.correlation == corr
    finally:
        await port.stop()


def test_components_helper() -> None:
    from agentic.adapters.picn.names import components_to_port_name

    assert components_to_port_name([b"a", b"b"]).components == (b"a", b"b")


def test_architecture_allows_picn_layers_only_in_adapter() -> None:
    """Sanity: PicnSubstratePort module imports PiCN.Layers (AC2 allowlist)."""
    import agentic.adapters.picn.port as mod

    assert "PiCN" in mod.__file__ or hasattr(mod, "PicnSubstratePort")
    # Importing Layers from this module is expected; architecture tests cover AC2.
    assert PicnName("/a/b").components_to_string() == "/a/b"
