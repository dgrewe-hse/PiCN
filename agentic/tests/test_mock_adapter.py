# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests for the in-memory mock substrate adapter."""

from __future__ import annotations

import asyncio
from typing import assert_type

import pytest

from agentic.adapters.mock import ManualClock, MockSubstratePort
from agentic.port import (
    EndpointRef,
    InboundRequest,
    Malformed,
    RequestFailed,
    RequestSent,
    RequestTimedOut,
    ResponseArrived,
    SubstrateEvent,
    SubstratePort,
    Unreachable,
)
from agentic.port.names import Name


def _name(*parts: str) -> Name:
    return Name(tuple(p.encode("utf-8") for p in parts))


async def _drain(queue: asyncio.Queue[SubstrateEvent], n: int) -> list[SubstrateEvent]:
    events: list[SubstrateEvent] = []
    for _ in range(n):
        events.append(await asyncio.wait_for(queue.get(), timeout=1.0))
    return events


@pytest.mark.asyncio
async def test_conforms_to_substrate_port_and_assert_type() -> None:
    port: SubstratePort = MockSubstratePort()
    assert isinstance(port, SubstratePort)
    assert_type(port, SubstratePort)


@pytest.mark.asyncio
async def test_happy_path_request_response() -> None:
    clock = ManualClock(1.0)
    mock = MockSubstratePort(clock=clock)
    inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=8)
    await mock.start(inbound)

    corr = b"\x01"
    mock.script_response(corr, b"ok")
    await mock.send_request(
        _name("cap", "svc"),
        b"req",
        correlation=corr,
        deadline=10.0,
    )
    sent, arrived = await _drain(inbound, 2)
    assert isinstance(sent, RequestSent)
    assert sent.correlation == corr
    assert sent.at == 1.0
    assert isinstance(arrived, ResponseArrived)
    assert arrived.payload == b"ok"
    assert arrived.correlation == corr
    await mock.stop()


@pytest.mark.asyncio
async def test_fail_next_emits_request_failed_not_raise() -> None:
    mock = MockSubstratePort()
    inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=8)
    await mock.start(inbound)

    mock.fail_next(Unreachable(detail="no route"))
    await mock.send_request(
        _name("a"),
        b"",
        correlation=b"c1",
        deadline=100.0,
    )
    sent, failed = await _drain(inbound, 2)
    assert isinstance(sent, RequestSent)
    assert isinstance(failed, RequestFailed)
    assert isinstance(failed.reason, Unreachable)
    assert failed.reason.detail == "no route"
    await mock.stop()


@pytest.mark.asyncio
async def test_malformed_and_timeout_injection() -> None:
    mock = MockSubstratePort()
    inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=8)
    await mock.start(inbound)

    mock.fail_on(b"bad", Malformed(detail="decode"))
    await mock.send_request(_name("x"), b"", correlation=b"bad", deadline=1.0)
    _, failed = await _drain(inbound, 2)
    assert isinstance(failed, RequestFailed)
    assert isinstance(failed.reason, Malformed)

    mock.timeout_next()
    await mock.send_request(_name("y"), b"", correlation=b"t1", deadline=1.0)
    _, timed = await _drain(inbound, 2)
    assert isinstance(timed, RequestTimedOut)
    assert timed.correlation == b"t1"
    await mock.stop()


@pytest.mark.asyncio
async def test_deadline_vs_clock_produces_timeout() -> None:
    clock = ManualClock(5.0)
    mock = MockSubstratePort(clock=clock)
    inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=8)
    await mock.start(inbound)

    await mock.send_request(
        _name("late"),
        b"",
        correlation=b"late",
        deadline=4.0,  # already past
    )
    _, timed = await _drain(inbound, 2)
    assert isinstance(timed, RequestTimedOut)
    await mock.stop()


@pytest.mark.asyncio
async def test_out_of_order_release_of_held_terminals() -> None:
    mock = MockSubstratePort(hold_terminals=True)
    inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=16)
    await mock.start(inbound)

    mock.script_response(b"\x01", b"first")
    mock.script_response(b"\x02", b"second")
    await mock.send_request(_name("a"), b"", correlation=b"\x01", deadline=10.0)
    await mock.send_request(_name("b"), b"", correlation=b"\x02", deadline=10.0)

    # Only RequestSent events so far
    sent1, sent2 = await _drain(inbound, 2)
    assert isinstance(sent1, RequestSent) and sent1.correlation == b"\x01"
    assert isinstance(sent2, RequestSent) and sent2.correlation == b"\x02"
    assert set(mock.buffered_correlations()) == {b"\x01", b"\x02"}
    assert inbound.empty()

    # Deliver responses in reverse order
    await mock.release_terminals([b"\x02", b"\x01"])
    r2, r1 = await _drain(inbound, 2)
    assert isinstance(r2, ResponseArrived) and r2.correlation == b"\x02"
    assert r2.payload == b"second"
    assert isinstance(r1, ResponseArrived) and r1.correlation == b"\x01"
    assert r1.payload == b"first"
    assert mock.buffered_correlations() == ()
    await mock.stop()


@pytest.mark.asyncio
async def test_register_lookup_and_unregister() -> None:
    mock = MockSubstratePort()
    inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=4)
    await mock.start(inbound)

    prefix = _name("cap", "issuer")
    endpoint = EndpointRef(value=b"face-1")
    await mock.register_prefix(prefix, endpoint)
    found = mock.lookup(_name("cap", "issuer", "svc"))
    assert found == (endpoint,)

    await mock.unregister_prefix(prefix, endpoint)
    assert mock.lookup(_name("cap", "issuer", "svc")) == ()
    await mock.stop()


@pytest.mark.asyncio
async def test_parse_name_and_inbound_request_roundtrip() -> None:
    mock = MockSubstratePort()
    inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=4)
    await mock.start(inbound)

    name = mock.parse_name("/cap/issuer/svc")
    assert name.components == (b"cap", b"issuer", b"svc")

    await mock.inject_inbound_request(name, b"ask", correlation=b"in-1")
    event = (await _drain(inbound, 1))[0]
    assert isinstance(event, InboundRequest)
    assert event.payload == b"ask"

    await mock.send_response(b"in-1", b"reply")
    assert mock.responses_sent[b"in-1"] == b"reply"
    await mock.stop()


@pytest.mark.asyncio
async def test_send_request_before_start_raises_runtime_error() -> None:
    """Programming error (not started) may raise; substrate failures must not."""
    mock = MockSubstratePort()
    with pytest.raises(RuntimeError, match="start"):
        await mock.send_request(_name("x"), b"", correlation=b"c", deadline=1.0)


def test_manual_clock_rejects_negative_advance() -> None:
    clock = ManualClock(0.0)
    with pytest.raises(ValueError):
        clock.advance(-1.0)
