# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""NFN must pass non-NFN capability Interests upward unmodified.

Placement of AgenticLayer above NFN depends on this: NFN claims only names
ending in the NFN marker; everything else is forwarded to the higher layer
unchanged (or reflected downward when NFN is topmost).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from PiCN.Layers.ICNLayer.ContentStore import ContentStoreMemoryExact
from PiCN.Layers.ICNLayer.ForwardingInformationBase import (
    ForwardingInformationBaseMemoryPrefix,
)
from PiCN.Layers.ICNLayer.PendingInterestTable import PendingInterstTableMemoryExact
from PiCN.Layers.LinkLayer.FaceIDTable import FaceIDDict
from PiCN.Layers.NFNLayer.AsyncBasicNFNLayer import AsyncBasicNFNLayer
from PiCN.Layers.NFNLayer.NFNComputationTable import NFNComputationList
from PiCN.Layers.NFNLayer.NFNExecutor import NFNPythonExecutor
from PiCN.Layers.NFNLayer.Parser import DefaultNFNParser
from PiCN.Layers.NFNLayer.R2C import TimeoutR2CHandler
from PiCN.Packets import Interest, Name
from PiCN.Processes import PiCNSyncDataStructFactory
from PiCN.Processes.AsyncLayerProcess import AsyncLayerProcess

from agentic.agentic_layer.naming import capability_name


class _CaptureLayer(AsyncLayerProcess):
    """Records packets received from lower."""

    def __init__(self) -> None:
        super().__init__(logger_name="Capture", log_level=255)
        self.received: asyncio.Queue[object] = asyncio.Queue()

    async def data_from_lower(self, to_lower, to_higher, data) -> None:
        await self.received.put(data)

    async def data_from_higher(self, to_lower, to_higher, data) -> None:
        return None


def _make_nfn_layer(executor: ThreadPoolExecutor) -> AsyncBasicNFNLayer:
    factory = PiCNSyncDataStructFactory()
    factory.register("cs", ContentStoreMemoryExact)
    factory.register("fib", ForwardingInformationBaseMemoryPrefix)
    factory.register("pit", PendingInterstTableMemoryExact)
    factory.register("computation_table", NFNComputationList)
    factory.register("faceidtable", FaceIDDict)
    factory.create_manager()

    cs = factory.manager.cs()
    fib = factory.manager.fib()
    pit = factory.manager.pit()
    faceidtable = factory.manager.faceidtable()
    r2c = TimeoutR2CHandler()
    parser = DefaultNFNParser()
    comp_table = factory.manager.computation_table(r2c, parser)
    return AsyncBasicNFNLayer(
        cs,
        fib,
        pit,
        faceidtable,
        comp_table,
        {"PYTHON": NFNPythonExecutor()},
        parser,
        r2c,
        log_level=255,
        executor=executor,
    )


@pytest.mark.asyncio
async def test_cap_interest_reaches_layer_above_nfn_unmodified() -> None:
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        nfn = _make_nfn_layer(pool)
        capture = _CaptureLayer()

        to_lower: asyncio.Queue = asyncio.Queue(maxsize=16)
        between: asyncio.Queue = asyncio.Queue(maxsize=16)

        nfn.queue_from_lower = asyncio.Queue(maxsize=16)
        nfn.queue_to_lower = to_lower
        nfn.queue_to_higher = between
        nfn.queue_from_higher = asyncio.Queue(maxsize=16)

        capture.queue_from_lower = between
        capture.queue_to_lower = asyncio.Queue(maxsize=16)
        capture.queue_to_higher = asyncio.Queue(maxsize=16)
        capture.queue_from_higher = asyncio.Queue(maxsize=16)

        nfn.start()
        capture.start()

        key = b"\x01" * 32
        agentic_name = capability_name(key, [b"hospital", b"beds"], "1.0.0")
        picn_name = Name(list(agentic_name.components))
        interest = Interest(picn_name)
        original_components = list(interest.name.components)

        await nfn.queue_from_lower.put([0, interest])
        delivered = await asyncio.wait_for(capture.received.get(), timeout=2.0)

        assert isinstance(delivered, list)
        packet = delivered[1]
        assert isinstance(packet, Interest)
        assert list(packet.name.components) == original_components
        assert packet.name.components[-1].startswith(b"v=")
        assert packet.name.components[-1] != b"NFN"

        await capture.stop()
        await nfn.stop()
    finally:
        pool.shutdown(wait=True)


@pytest.mark.asyncio
async def test_non_nfn_still_reflects_down_when_nfn_is_topmost() -> None:
    """Preserve prior behaviour when no higher layer is wired."""
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        nfn = _make_nfn_layer(pool)
        to_lower: asyncio.Queue = asyncio.Queue(maxsize=16)
        nfn.queue_to_lower = to_lower
        interest = Interest(Name("/cap/issuer/svc/v=1"))
        await nfn.data_from_lower(to_lower, None, [0, interest])
        out = await asyncio.wait_for(to_lower.get(), timeout=1.0)
        assert out[1].name.components == interest.name.components
    finally:
        pool.shutdown(wait=True)
