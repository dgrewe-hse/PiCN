# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution under the BSD 3-Clause License; see the LICENSE file for text.

"""Targeted branch coverage for PicnSubstratePort response/demux internals."""

from __future__ import annotations

import asyncio
import time

import pytest

from PiCN.Packets import Content, Nack, NackReason, Name as PicnName

from agentic.adapters.picn import PicnSubstratePort
from agentic.port.events import RequestSent
from agentic.port.names import Name


@pytest.mark.asyncio
async def test_put_before_start_soft_drops() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    # Never started: _inbound is None and _started False → soft drop.
    await port._put(
        RequestSent(correlation=b"c", name=Name((b"x",)), at=0.0)
    )


@pytest.mark.asyncio
async def test_cancel_deadline_with_done_task() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    task = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0)
    port._deadline_tasks[b"c"] = task
    port._cancel_deadline(b"c")
    assert b"c" not in port._deadline_tasks


@pytest.mark.asyncio
async def test_deliver_content_without_outstanding_is_dropped() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    try:
        await port._deliver_uplink_packet(
            [0, Content(PicnName("/nope"), b"x")]
        )
        assert inbound.empty()
    finally:
        await port.stop()


@pytest.mark.asyncio
async def test_deliver_content_bytes_and_str_payloads() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    try:
        corr = b"\x21" * 32
        port._outstanding[corr] = Name((b"net",))
        await port._deliver_uplink_packet([0, Content(PicnName("/net"), b"raw-bytes")])
        event = await asyncio.wait_for(inbound.get(), timeout=1.0)
        assert event.correlation == corr
        assert event.payload == b"raw-bytes"

        corr2 = b"\x22" * 32
        port._outstanding[corr2] = Name((b"net2",))
        await port._deliver_uplink_packet([0, Content(PicnName("/net2"), "as-str")])
        event2 = await asyncio.wait_for(inbound.get(), timeout=1.0)
        assert event2.correlation == corr2
        assert event2.payload == b"as-str"
    finally:
        await port.stop()


@pytest.mark.asyncio
async def test_nack_with_no_outstanding_is_dropped() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    try:
        await port._deliver_uplink_packet(
            [0, Nack(PicnName("/n"), NackReason.NO_ROUTE, None)]
        )
        assert inbound.empty()
    finally:
        await port.stop()


@pytest.mark.asyncio
async def test_stop_without_start_is_a_noop() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    await port.stop()


def test_response_sink_is_forwarder_backed() -> None:
    calls: list[tuple[bytes, bytes]] = []

    async def sink(correlation: bytes, payload: bytes) -> None:
        calls.append((correlation, payload))

    async def run() -> None:
        port = PicnSubstratePort("127.0.0.1", 9, log_level=255, response_sink=sink)
        await port.send_response(b"\xaa" * 32, b"body")

    asyncio.run(run())
    assert calls == [(b"\xaa" * 32, b"body")]


@pytest.mark.asyncio
async def test_deadline_watch_fires_timeout() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    try:
        corr = b"\x23" * 32
        port._outstanding[corr] = Name((b"late",))
        task = asyncio.create_task(
            port._deadline_watch(corr, Name((b"late",)), 0.0)
        )
        port._deadline_tasks[corr] = task
        event = await asyncio.wait_for(inbound.get(), timeout=1.0)
        assert event.correlation == corr
    finally:
        await port.stop()


@pytest.mark.asyncio
async def test_deadline_watch_cancelled_returns() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    task = asyncio.create_task(
        port._deadline_watch(b"z" * 32, Name((b"z",)), 5.0)
    )
    await asyncio.sleep(0)
    task.cancel()
    await task


def test_send_request_estimates_deadline() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    assert port._clock() >= 0.0


@pytest.mark.asyncio
async def test_async_deadline_task_named() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    try:
        corr = b"\x24" * 32
        # monkeypatch the stack put so no real UDP is needed
        original_put = port._lstack.queue_from_higher.put

        async def fake_put(_item: object) -> None:
            return None

        port._lstack.queue_from_higher.put = fake_put  # type: ignore[method-assign]
        try:
            await port.send_request(
                Name((b"cap", b"x")), b"", correlation=corr, deadline=time.monotonic() + 30.0
            )
        finally:
            port._lstack.queue_from_higher.put = original_put  # type: ignore[method-assign]
        assert isinstance(await asyncio.wait_for(inbound.get(), timeout=1.0), RequestSent)
        await asyncio.sleep(0)
    finally:
        await port.stop()

@pytest.mark.asyncio
async def test_start_twice_keeps_single_stack_start() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    await port.start(inbound)  # stack already started: skip start_all
    assert port._stack_started is True
    await port.stop()


@pytest.mark.asyncio
async def test_put_hard_fails_when_started_without_inbound() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    port._started = True
    port._inbound = None
    with pytest.raises(RuntimeError):
        await port._put(RequestSent(correlation=b"c", name=Name((b"x",)), at=0.0))


def test_match_outstanding_forward_prefix() -> None:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    port._outstanding[b"corr"] = Name((b"a", b"b"))
    # Interest name is a prefix of the Content name → forward directional match.
    assert port._match_outstanding(Name((b"a", b"b", b"c"))) == b"corr"
    assert port._match_outstanding(Name((b"z",))) is None
