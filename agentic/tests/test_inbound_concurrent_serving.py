# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Concurrent inbound-request serving on the edge forwarder (PR-3).

Dennis-approved behavioural change (Experiment D, design v5.1 §3.1 option a):
the port-less/forwarder inbound request pump must serve ``InboundRequest``\\ s
concurrently — one serving task per request — so multiple producer
``hub.invoke`` calls overlap. Serial serving collapses the client-side
concurrent fan-out to ~1.0x end-to-end speedup because the edge pays
~k x latency regardless of dispatch mode.

Assertions:

1. wall-clock: N concurrent inbound requests complete in ~max(latency), not
   ~sum(latency) — RED while the pump serves inline-sequentially;
2. end-to-end over the bus: paired serial/concurrent cells keep byte-identical
   Merkle trace roots and both collapse from ~k x latency to ~max(latency)
   (pre-change the serialized edge pump made both pay the k x latency bound);
3. bounded discipline: the number of concurrently served requests never
   exceeds the inbound queue bound (ADR-005);
4. shutdown: ``stop_port`` cancels in-flight serving tasks promptly.

Run: ``python -m pytest agentic/tests/test_inbound_concurrent_serving.py -v``
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from typing import Any

import pytest
from PiCN.Layers.LinkLayer.Interfaces import SimulationBus
from PiCN.Layers.PacketEncodingLayer.Encoder import NdnTlvEncoder
from PiCN.Packets import Interest, Name as PicnName
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

from agentic.adapters.mock import MockSubstratePort
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.binding import DeterministicBackend, LatencyBackend
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact
from demo.bus_topology import unblock_sim_interfaces
from demo.concurrency_topology import run_concurrency_cell

INPUT_SCHEMA = {
    "type": "object",
    "properties": {"patient_id": {"type": "string"}},
    "required": [],
    "additionalProperties": True,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"beds_free": {"type": "integer"}},
    "required": ["beds_free"],
    "additionalProperties": False,
}


def _descriptor_with_name(name: Name) -> Any:
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
    return replace(desc, name=name)


async def _drain_until(predicate: Any, *, tries: int = 2000, delay: float = 0.005) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(delay)
    return predicate()


async def _build_forwarder(tag: str) -> AgenticForwarder:
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder, log_level=255)
    return AgenticForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface(f"edge-{tag}")],
        log_level=255,
        ageing_interval=1,
        runtime=Runtime.ASYNC,
    )


@pytest.mark.asyncio
async def test_concurrent_inbound_serving_wall_clock() -> None:
    """4 concurrent inbound requests complete in ~max(latency), not ~sum(latency).

    Serial serving costs 4 x 0.15 s = 0.60 s; concurrent serving ~0.15 s. The
    bound 0.45 s (75% of serial) can only be met when ``hub.invoke`` overlaps.
    """
    fwd = await _build_forwarder("concurrent-serving")
    layer = fwd.agentic
    k = 4
    latency_s = 0.15
    leaf_names: list[Name] = []
    for i in range(k):
        wire_name = Name((b"cap", b"fwd", b"hospital", b"beds", f"h{i}".encode()))
        desc = _descriptor_with_name(wire_name)
        fwd.register_capability(
            desc,
            LatencyBackend(
                DeterministicBackend(
                    lambda payload, _i=i: {"beds_free": 10 + _i},
                    input_schema=INPUT_SCHEMA,
                    output_schema=OUTPUT_SCHEMA,
                ),
                latency_s=latency_s,
            ),
            backend_label="latency",
        )
        leaf_names.append(wire_name)

    await fwd.start_forwarder_async()
    raw_seam = layer.queue_to_lower
    assert raw_seam is not None
    captured: list[Any] = []

    class _SeamSpy:
        async def put(self, item: Any) -> None:
            captured.append(item)
            await raw_seam.put(item)

    layer.queue_to_lower = _SeamSpy()  # type: ignore[assignment]
    try:
        started = time.perf_counter()
        for i in range(k):
            await fwd.icnlayer.queue_from_lower.put(
                [7, Interest(PicnName(list(leaf_names[i].components)))]
            )
        ok = await _drain_until(lambda: len(captured) >= k)
        elapsed = time.perf_counter() - started
        assert ok, "not all inbound requests produced a seam response"

        # Per-leaf correctness: every leaf's response carries its own value
        # (no cross-talk under concurrent serving).
        beds_by_leaf: dict[bytes, int] = {}
        for item in captured:
            body = json.loads(item[1].get_bytes().decode("utf-8"))
            assert body["input_rejected"] is False
            assert body["payload"] is not None
            leaf_tag = bytes(item[1].name.components[-1])
            beds_by_leaf[leaf_tag] = body["payload"]["beds_free"]
        assert beds_by_leaf == {f"h{i}".encode(): 10 + i for i in range(k)}

        # The benefit: concurrent serving is meaningfully faster than the
        # serial sum-of-service-times bound.
        assert elapsed < latency_s * k * 0.75, (
            f"edge served {k} x {latency_s}s requests in {elapsed:.3f}s; "
            f"serial bound {latency_s * k:.3f}s not beaten"
        )
    finally:
        unblock_sim_interfaces(fwd)
        await fwd.stop_forwarder_async()


@pytest.mark.asyncio
async def test_trace_root_preserved_and_fanout_benefit_end_to_end() -> None:
    """Paired bus cells: identical trace roots and ~max(latency) wall-clock.

    Pre-change the inline-sequential edge pump made BOTH dispatch modes pay
    ~k x latency over the bus, collapsing the fan-out benefit (paired speedup
    ~1.0 with ~4 x latency wall-clock at k=4). With concurrent serving each
    mode completes in ~max(latency) — the real benefit end-to-end — while the
    committed Merkle trace root stays byte-identical across the paired cells.
    """
    k = 4
    leaf_latency_s = 0.1
    serial = await run_concurrency_cell(
        k=k, seed=11, leaf_latency_s=leaf_latency_s, dispatch="serial", log_level=255
    )
    concurrent = await run_concurrency_cell(
        k=k, seed=11, leaf_latency_s=leaf_latency_s, dispatch="concurrent", log_level=255
    )
    # Trace root correctness under concurrent serving: paired same-leaf-set
    # cells must aggregate to byte-identical Merkle roots.
    assert serial.trace_root_hex == concurrent.trace_root_hex
    assert serial.expected_subintent_set == concurrent.expected_subintent_set
    # End-to-end benefit: the serial-serving bound (~k x latency) is beaten by
    # BOTH modes — 300 ms bound at k=4/0.1 s, ~100 ms measured post-change.
    serialized_bound_ms = leaf_latency_s * k * 0.75 * 1000.0
    assert concurrent.elapsed_ms < serialized_bound_ms, (
        f"concurrent cell {concurrent.elapsed_ms:.1f} ms still pays the "
        f"serialized-serving bound {serialized_bound_ms:.1f} ms"
    )
    assert serial.elapsed_ms < serialized_bound_ms, (
        f"serial cell {serial.elapsed_ms:.1f} ms still pays the "
        f"serialized-serving bound {serialized_bound_ms:.1f} ms"
    )


@pytest.mark.asyncio
async def test_serving_bound_respects_inbound_size() -> None:
    """Concurrently served requests never exceed the inbound queue bound."""
    layer = AgenticLayer(runtime="async", inbound_size=2)
    layer.queue_to_lower = asyncio.Queue()
    bound = 2
    total = 6
    counter = {"cur": 0, "peak": 0}

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        counter["cur"] += 1
        counter["peak"] = max(counter["peak"], counter["cur"])
        await asyncio.sleep(0.02)
        counter["cur"] -= 1
        return {"beds_free": 1}

    for i in range(total):
        wire_name = Name((b"svc", b"t", f"{i}".encode()))
        desc = _descriptor_with_name(wire_name)
        layer.register_capability(
            desc,
            DeterministicBackend(
                handler,
                input_schema=INPUT_SCHEMA,
                output_schema=OUTPUT_SCHEMA,
            ),
            backend_label="deterministic",
        )

    async def drive(i: int) -> None:
        await layer.data_from_lower(None, None, [i, Interest(PicnName(f"/svc/t/{i}"))])

    tasks = [asyncio.create_task(drive(i)) for i in range(total)]
    await asyncio.gather(*tasks)
    for _ in range(total):
        await asyncio.wait_for(layer.queue_to_lower.get(), timeout=5.0)
    assert counter["cur"] == 0
    # Bounded (<= inbound_size) but genuinely parallel (>1).
    assert counter["peak"] == bound, (
        f"peak concurrent serving {counter['peak']} != inbound_size bound {bound}"
    )


@pytest.mark.asyncio
async def test_stop_port_cancels_inflight_serving() -> None:
    """stop_port() cancels in-flight serving tasks promptly (no hang)."""
    port = MockSubstratePort()
    layer = AgenticLayer(runtime="async", port=port)
    wire_name = Name((b"svc", b"slow"))
    desc = _descriptor_with_name(wire_name)
    started_invoke = asyncio.Event()

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        started_invoke.set()
        await asyncio.sleep(10.0)
        return {"beds_free": 1}

    class _SlowBackend:
        def declared_schemas(self) -> tuple[dict[str, Any], dict[str, Any]]:
            return (INPUT_SCHEMA, OUTPUT_SCHEMA)

        async def invoke(
            self, payload: dict[str, Any], *, deadline: float
        ) -> dict[str, Any]:
            return await handler(payload)

    layer.register_capability(desc, _SlowBackend(), backend_label="slow")
    await layer.start_port()
    await port.inject_inbound_request(desc.name, b"{}", correlation=b"\x07" * 32)
    await asyncio.wait_for(started_invoke.wait(), timeout=2.0)

    stop_started = time.perf_counter()
    await asyncio.wait_for(layer.stop_port(), timeout=2.0)
    assert time.perf_counter() - stop_started < 5.0
