"""NF-6 parity test: non-NFN Content from higher passes downward via the core.

The shared core :meth:`NFNLayerCore.handle_from_higher` must mirror the existing
upward ``handle_interest`` pass-through for a downward ``Content`` that NFN does
not own: both the sync ``BasicNFNLayer`` and the async ``AsyncBasicNFNLayer``
must emit an identical ``Outbound("queue_lower", [packet_id, content])``.

NFN-marked / R2C ``Content`` and ``Nack`` keep their prior (empty) behaviour.
A malformed item returns empty without raising.
"""

from __future__ import annotations

import asyncio
import queue
from concurrent.futures import ThreadPoolExecutor

import pytest

from PiCN.Layers.ICNLayer.ContentStore import ContentStoreMemoryExact
from PiCN.Layers.ICNLayer.ForwardingInformationBase import (
    ForwardingInformationBaseMemoryPrefix,
)
from PiCN.Layers.ICNLayer.PendingInterestTable import PendingInterstTableMemoryExact
from PiCN.Layers.LinkLayer.FaceIDTable import FaceIDDict
from PiCN.Layers.NFNLayer import BasicNFNLayer
from PiCN.Layers.NFNLayer.AsyncBasicNFNLayer import AsyncBasicNFNLayer
from PiCN.Layers.NFNLayer.NFNComputationTable import NFNComputationList
from PiCN.Layers.NFNLayer.NFNExecutor import NFNPythonExecutor
from PiCN.Layers.NFNLayer.Parser import DefaultNFNParser
from PiCN.Layers.NFNLayer.R2C import TimeoutR2CHandler
from PiCN.Packets import Content, Interest, Name, Nack, NackReason
from PiCN.Processes import PiCNSyncDataStructFactory


def _make_sync_layer() -> BasicNFNLayer:
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
    layer = BasicNFNLayer(
        cs,
        fib,
        pit,
        faceidtable,
        comp_table,
        {"PYTHON": NFNPythonExecutor()},
        parser,
        r2c,
        log_level=255,
    )
    layer.queue_to_lower = queue.Queue()
    layer.queue_from_lower = queue.Queue()
    return layer


def _make_async_layer() -> AsyncBasicNFNLayer:
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
    layer = AsyncBasicNFNLayer(
        cs,
        fib,
        pit,
        faceidtable,
        comp_table,
        {"PYTHON": NFNPythonExecutor()},
        parser,
        r2c,
        log_level=255,
    )
    return layer


def _sync_outbounds(data) -> list:
    layer = _make_sync_layer()
    layer.data_from_higher(layer.queue_to_lower, None, data)
    out = []
    while not layer.queue_to_lower.empty():
        out.append(layer.queue_to_lower.get_nowait())
    return out


@pytest.mark.asyncio
async def _async_outbounds(data) -> list:
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        layer = _make_async_layer()
        layer.queue_to_lower = asyncio.Queue(maxsize=16)
        await layer.data_from_higher(layer.queue_to_lower, None, data)
        out = []
        while not layer.queue_to_lower.empty():
            out.append(layer.queue_to_lower.get_nowait())
        return out
    finally:
        pool.shutdown(wait=True)


# --- the downward pass-through ------------------------------------------------


def test_non_nfn_content_sync_passes_down() -> None:
    content = Content(Name("/cap/responder/result/v=1"), b"payload")
    out = _sync_outbounds([5, content])
    assert out == [[5, content]]


@pytest.mark.asyncio
async def test_non_nfn_content_async_passes_down() -> None:
    content = Content(Name("/cap/responder/result/v=1"), b"payload")
    out = await _async_outbounds([5, content])
    assert out == [[5, content]]


@pytest.mark.asyncio
async def test_sync_and_async_identical() -> None:
    content = Content(Name("/cap/responder/result/v=1"), b"payload")
    sync_out = _sync_outbounds([9, content])
    async_out = await _async_outbounds([9, content])
    assert sync_out == async_out


# --- NFN-owned / R2C / Nack keep prior empty behaviour ------------------------


def test_nfn_marked_content_not_passed_down() -> None:
    nfn_name = Name("/func/f1")
    nfn_name += "_()"
    nfn_name += "NFN"
    content = Content(nfn_name, b"payload")
    assert content.name.components[-1] == b"NFN"
    assert _sync_outbounds([5, content]) == []


def test_r2c_content_not_passed_down() -> None:
    r2c_name = Name("/func/f1")
    r2c_name += "R2C"
    r2c_name += "KEEPALIVE"
    r2c_name += "NFN"
    content = Content(r2c_name, b"payload")
    assert _sync_outbounds([5, content]) == []


def test_nack_not_passed_down() -> None:
    nack = Nack(Name("/cap/x"), NackReason.NO_ROUTE, interest=None)
    assert _sync_outbounds([5, nack]) == []


def test_interest_from_higher_routes_to_handle_interest() -> None:
    # An Interest is owned by the existing handle_interest path; a non-NFN
    # Interest with no higher layer reflects downward.
    name = Name("/cap/responder/result/v=1")
    out = _sync_outbounds([5, Interest(name)])
    assert out == [[5, Interest(name)]]


def test_malformed_returns_empty() -> None:
    assert _sync_outbounds([1, "not a packet"]) == []
    assert _sync_outbounds([]) == []
    assert _sync_outbounds(None) == []


def test_stateful_layer_reused() -> None:
    """Direct core call must not mutate state for a pass-through Content."""
    layer = _make_sync_layer()
    content = Content(Name("/cap/x/v=1"), b"z")
    layer.data_from_higher(layer.queue_to_lower, None, [3, content])
    assert layer.computation_table.get_container_size() == 0
    assert layer.queue_to_lower.get_nowait() == [3, content]