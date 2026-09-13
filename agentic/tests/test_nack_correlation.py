# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Nack demux correlates by name; no match → drop (not mis-attribute).

The wire carries no correlation token, so a Nack is matched to an outstanding
request by its name. An unmatched Nack must be dropped, never attributed to an
arbitrary in-flight request.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from PiCN.Packets import Interest, Nack, NackReason, Name as PicnName

from agentic.adapters.picn import PicnSubstratePort
from agentic.port.events import RequestFailed, RequestSent
from agentic.port.names import Name


async def _started_port() -> tuple[PicnSubstratePort, asyncio.Queue]:
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    inbound: asyncio.Queue = asyncio.Queue()
    await port.start(inbound)
    return port, inbound


@pytest.mark.asyncio
async def test_nack_correlates_to_matching_name(monkeypatch: pytest.MonkeyPatch) -> None:
    port, inbound = await _started_port()

    async def fake_put(_item: object) -> None:
        return None

    calls = {"n": 0}

    async def fake_get() -> list:
        calls["n"] += 1
        if calls["n"] > 1:
            await asyncio.Event().wait()
        return [0, Nack(PicnName("/cap/target"), NackReason.NO_ROUTE, Interest(PicnName("/cap/target")))]

    monkeypatch.setattr(port._lstack.queue_from_higher, "put", fake_put)
    monkeypatch.setattr(port._lstack.queue_to_higher, "get", fake_get)
    try:
        corr = b"\x0a" * 32
        await port.send_request(
            Name((b"cap", b"target")), b"", correlation=corr, deadline=time.monotonic() + 5.0
        )
        assert isinstance(await asyncio.wait_for(inbound.get(), timeout=1.0), RequestSent)
        failed = await asyncio.wait_for(inbound.get(), timeout=1.0)
        assert isinstance(failed, RequestFailed)
        assert failed.correlation == corr
    finally:
        await port.stop()


@pytest.mark.asyncio
async def test_unmatched_nack_is_dropped_not_misattributed(monkeypatch: pytest.MonkeyPatch) -> None:
    port, inbound = await _started_port()

    async def fake_put(_item: object) -> None:
        return None

    calls = {"n": 0}

    async def fake_get() -> list:
        calls["n"] += 1
        if calls["n"] > 1:
            await asyncio.Event().wait()
        return [0, Nack(PicnName("/other/name"), NackReason.NO_ROUTE, Interest(PicnName("/other/name")))]

    monkeypatch.setattr(port._lstack.queue_from_higher, "put", fake_put)
    monkeypatch.setattr(port._lstack.queue_to_higher, "get", fake_get)
    try:
        corr = b"\x0b" * 32
        await port.send_request(
            Name((b"cap", b"target")), b"", correlation=corr, deadline=time.monotonic() + 5.0
        )
        assert isinstance(await asyncio.wait_for(inbound.get(), timeout=1.0), RequestSent)
        # The unmatched Nack must not fail the outstanding request.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(inbound.get(), timeout=0.2)
        assert corr in port._outstanding
    finally:
        await port.stop()