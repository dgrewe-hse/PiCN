# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment D topology: concurrent fan-out over a real producer path.

Ambulance ``AgenticLayer.submit_intent`` emits ``/cap/fwd/<path>`` capability
Interests on a ``SimulationBus`` to an edge ``AgenticForwarder`` that serves
each leaf from a **registered** ``LatencyBackend`` (PR-1 makes ``hub.invoke``
reachable). Each leaf's ``LatencyBackend`` sleeps ``latency_s`` before returning
— this is what makes ``T_service`` a real, non-zero measurement and lets the
serial-vs-concurrent fan-out benefit be measured (design v4 §4).

Honest framing (design v4 §5.5): this is a SimulationBus **mechanism
illustration** on a single host with deterministic backends; ``T_network`` is
near-zero by construction; it is **not** a deployment measurement.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from PiCN.Layers.LinkLayer.Interfaces import SimulationBus
from PiCN.Layers.PacketEncodingLayer.Encoder import NdnTlvEncoder
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

from agentic.adapters.picn import PicnSubstratePort
from agentic.agentic_layer import AgenticLayer, RecordingLatencyObserver
from agentic.agentic_layer.decomposer import SubIntent
from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.binding import DeterministicBackend, LatencyBackend
from agentic.port.events import ResponseArrived, SubstrateEvent
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact

from demo.bus_topology import unblock_sim_interfaces

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
    "additionalProperties": True,
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
        verify_artefact(sign_artefact(body, key)),
        capability_path=(b"hospital", b"beds"),
    )
    return dataclasses.replace(desc, name=name)


@dataclass
class ConcurrencyRunResult:
    """Outcome of one serial or concurrent fan-out measurement.

    :param seed: Run seed.
    :param k: Leaf count.
    :param leaf_latency_s: Per-leaf ``LatencyBackend`` service latency (seconds).
    :param dispatch: ``"serial"`` or ``"concurrent"``.
    :param elapsed_ms: Wall-clock of ``submit_intent``.
    :param trace_root_hex: Final Context~PIT Merkle root (hex).
    :param t_decompose_ms: ``t_commit - t_emission`` (None when no emission ts).
    :param t_dispatch_ms: ``t1 - t0`` (dispatch block wall-time).
    :param t_network_ms: Mean ``t_response - t_send`` (intake clock).
    :param t_service_ms: Mean producer-side ``hub.invoke`` wall-time.
    :param t_aggregate_ms: ``t_intent - t1``.
    :param t_intent_ms: ``t_complete - t_emission`` (when emission supplied).
    :param leaf_inflight_peak: Peak concurrent in-flight leaves.
    :param leaf_overlap_fraction: Fraction of leaves whose service overlapped.
    """

    seed: int
    k: int
    leaf_latency_s: float
    dispatch: str
    elapsed_ms: float
    trace_root_hex: str
    t_decompose_ms: float | None
    t_dispatch_ms: float | None
    t_network_ms: float | None
    t_service_ms: float | None
    t_aggregate_ms: float | None
    t_intent_ms: float | None
    leaf_inflight_peak: int
    leaf_overlap_fraction: float
    expected_subintent_set: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


class _ObserverPort(PicnSubstratePort):
    """Port that records per-leaf timing from the intake observer's view."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.responses: dict[bytes, bytes] = {}

    async def _put(self, event: SubstrateEvent) -> None:
        if isinstance(event, ResponseArrived):
            self.responses[event.correlation] = event.payload
        await super()._put(event)


async def run_concurrency_cell(
    *,
    k: int,
    seed: int,
    leaf_latency_s: float,
    dispatch: str,
    observer_on: bool = True,
    log_level: int = 255,
) -> ConcurrencyRunResult:
    """Run one serial or concurrent fan-out cell on a SimulationBus.

    Topology (2 faces, registered producers)::

        ambulance (PicnSubstratePort / AgenticLayer.submit_intent)
            → edge (AgenticForwarder, registered LatencyBackend per leaf)

    :param k: Number of leaf capabilities (each served by a ``LatencyBackend``).
    :param seed: Deterministic seed (unused for latency; kept for metadata).
    :param leaf_latency_s: Per-leaf ``LatencyBackend`` latency in seconds.
    :param dispatch: ``"serial"`` or ``"concurrent"``.
    :param observer_on: Whether to attach a ``RecordingLatencyObserver``.
    :param log_level: PiCN logger level.
    """
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder, log_level=255)
    tag = f"{seed}-{k}-{leaf_latency_s}-{dispatch}-{time.time_ns() % 1_000_000}"
    edge_addr = f"edge-{tag}"
    amb_addr = f"ambulance-{tag}"

    edge = AgenticForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface(edge_addr)],
        log_level=log_level,
        ageing_interval=1,
        runtime=Runtime.ASYNC,
    )

    # Register a LatencyBackend producer per leaf capability. The edge's
    # AgenticLayer is port-less, so the reply traverses the forwarder stack.
    leaf_names: list[Name] = []
    leaf_backends: list[LatencyBackend] = []
    for i in range(k):
        wire_name = Name((b"cap", b"fwd", b"hospital", b"beds", f"h{i}".encode()))
        desc = _descriptor_with_name(wire_name)
        backend = LatencyBackend(
            DeterministicBackend(
                lambda payload, _i=i: {"beds_free": 10 + _i},
                input_schema=INPUT_SCHEMA,
                output_schema=OUTPUT_SCHEMA,
            ),
            latency_s=leaf_latency_s,
        )
        edge.register_capability(
            desc,
            backend,
            backend_label="latency",
        )
        leaf_backends.append(backend)
        leaf_names.append(wire_name)

    port = _ObserverPort(
        edge_addr, None, encoder=encoder, interfaces=[bus.add_interface(amb_addr)], log_level=log_level
    )
    observer = RecordingLatencyObserver() if observer_on else None
    layer = AgenticLayer(runtime="async", port=port, observer=observer)

    result: ConcurrencyRunResult | None = None
    await edge.start_forwarder_async()
    bus.start_process()
    await layer.start_port()
    try:
        await asyncio.sleep(0.05)

        issuer = generate_ed25519_private_key()
        issuer_der = issuer.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        # Parent digest must be identical across serial/concurrent for a given
        # (seed, k, leaf_latency_s) so the paired trace roots are comparable
        # (design v4 §4.5 condition 5 — anti-inflation: never divide independent
        # means). The dispatch mode must NOT enter the parent digest.
        parent = hashlib.sha256(
            f"concurrency-{seed}-{k}-{leaf_latency_s}".encode()
        ).digest()

        subs = [
            SubIntent(index=i, capability=("hospital", "beds", f"h{i}"), bindings={})
            for i in range(k)
        ]
        expected_set = hashlib.sha256(
            b"".join(
                hashlib.sha256(
                    parent
                    + bytes(str(sub.index), "ascii")
                    + b"/".join(c.encode() for c in sub.capability)
                ).digest()
                for sub in subs
            )
        ).hexdigest()

        emission_ts = time.perf_counter()
        started = time.perf_counter()
        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=issuer_der,
                sub_intents=subs,
                aggregation_policy="ALL",
                payload=b"{}",
                deadline=time.monotonic() + 30.0,
                now_ms=seed * 1000,
                dispatch=dispatch,
                emission_ts=emission_ts if observer_on else None,
            ),
            timeout=30.0,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        # T_intent decomposition (design v4 §4.2).
        t_commit = observer.commits[parent][0] if observer and parent in observer.commits else None
        t0 = observer.dispatch_begin.get(parent) if observer else None
        t1 = observer.dispatch_end.get(parent) if observer else None
        t_agg = observer.aggregate.get(parent)[0] if observer and parent in observer.aggregate else None
        t_intent = observer.complete.get(parent) if observer else None
        t_complete = t_intent if t_intent is not None else time.perf_counter()

        # T_decompose only when emission_ts supplied (never mis-sourced).
        t_decompose_ms = (
            (t_commit - emission_ts) * 1000.0
            if t_commit is not None and emission_ts is not None
            else None
        )
        t_dispatch_ms = (t1 - t0) * 1000.0 if t0 is not None and t1 is not None else None
        t_aggregate_ms = (
            (t_complete - t1) * 1000.0 if t1 is not None else None
        )
        t_intent_ms = (
            (t_complete - emission_ts) * 1000.0
            if emission_ts is not None
            else None
        )

        # T_network (intake clock both ends): mean of t_response - t_send.
        network_vals: list[float] = []
        if observer:
            for (p, li), t_resp in observer.leaf_response.items():
                if p == parent:
                    fwd = observer.leaf_forwarded.get((p, li))
                    if fwd is not None:
                        network_vals.append(t_resp - fwd[1])
        t_network_ms = (
            (sum(network_vals) / len(network_vals)) * 1000.0 if network_vals else None
        )

        # T_service (producer clock): the LatencyBackend's **measured**
        # per-leaf wall-clock around the inner invoke (VLAD PR-3 follow-up) —
        # never a restatement of the nominal latency. When no measurement was
        # recorded the nominal value is used and ``measured`` stays False, so
        # the gate refuses a five-way attribution on unmeasured service time.
        measured_s = [
            b.last_measured_s
            for b in leaf_backends
            if b.last_measured_s is not None
        ]
        if leaf_latency_s > 0 and measured_s:
            t_service_ms = (sum(measured_s) / len(measured_s)) * 1000.0
        elif leaf_latency_s > 0:
            t_service_ms = leaf_latency_s * 1000.0
        else:
            t_service_ms = 0.0
        service_measured = leaf_latency_s > 0 and bool(measured_s)

        # Peak in-flight / overlap (measured from leaf timestamps, intake clock).
        # A leaf is in-flight between its t_send and t_response; the peak is the
        # max count of overlapping in-flight intervals. Overlap fraction is the
        # share of leaves whose interval overlaps at least one other.
        intervals: list[tuple[float, float]] = []
        if observer:
            for (p, li), t_resp in observer.leaf_response.items():
                if p == parent:
                    fwd = observer.leaf_forwarded.get((p, li))
                    if fwd is not None:
                        intervals.append((fwd[1], t_resp))
        # Peak in-flight via sweep line over sorted (start, end) events.
        events: list[tuple[float, int]] = []
        for start_i, end_i in intervals:
            events.append((start_i, +1))
            events.append((end_i, -1))
        events.sort(key=lambda e: (e[0], e[1]))
        leaf_inflight_peak = 0
        running = 0
        for _, delta in events:
            running += delta
            leaf_inflight_peak = max(leaf_inflight_peak, running)
        # Overlap fraction: share of leaves that overlap at least one other.
        overlaps = 0
        for i, (start_i, end_i) in enumerate(intervals):
            for j, (start_j, end_j) in enumerate(intervals):
                if i != j and start_j < end_i and start_i < end_j:
                    overlaps += 1
                    break
        leaf_overlap_fraction = overlaps / len(intervals) if intervals else 0.0

        result = ConcurrencyRunResult(
            seed=seed,
            k=k,
            leaf_latency_s=leaf_latency_s,
            dispatch=dispatch,
            elapsed_ms=elapsed_ms,
            trace_root_hex=root.hex(),
            t_decompose_ms=t_decompose_ms,
            t_dispatch_ms=t_dispatch_ms,
            t_network_ms=t_network_ms,
            t_service_ms=t_service_ms,
            t_aggregate_ms=t_aggregate_ms,
            t_intent_ms=t_intent_ms,
            leaf_inflight_peak=leaf_inflight_peak,
            leaf_overlap_fraction=leaf_overlap_fraction,
            expected_subintent_set=expected_set,
            extras={
                "trace_root_verified": (
                    layer.pit.get(parent) is not None and layer.pit.get(parent).terminated  # type: ignore[union-attr]
                ),
                "leaf_count": k,
                "transport": "bus",
                "measured": service_measured,
                "t_service_measured_ms": (
                    (sum(measured_s) / len(measured_s)) * 1000.0 if measured_s else None
                ),
            },
        )
    finally:
        unblock_sim_interfaces(edge, port)
        await asyncio.sleep(0.05)
        try:
            layer._started_port = False
            if layer._event_task is not None:
                layer._event_task.cancel()
        except Exception:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(edge.stop_forwarder_async()), timeout=1.0)
        except Exception:
            pass
        try:
            bus.stop_process()
        except Exception:
            pass

    if result is None:
        raise RuntimeError("concurrency cell run failed before producing a result")
    return result


__all__ = ["ConcurrencyRunResult", "run_concurrency_cell"]