# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment E topology: physical multi-edge fan-out over real UDP.

Wiring (design v4 §5.1): the intake is an ``AgenticLayer`` with **one
``PicnSubstratePort`` per edge** (``peer_host``/``peer_port`` real-UDP branch),
and a facade (:class:`MultiEdgePort`) implementing the ``SubstratePort``
protocol routes each leaf to its owning edge by directional LPM over the
hospital capability prefixes. Edges are ``AgenticForwarder`` nodes with
registered producers; ``--edges N`` (N >= 1) splits the leaf capabilities
contiguously, N=1 is the single-edge control cell.

On a real deployment the edge hosts are remote (``edge_endpoints`` given);
without endpoints the same topology is built in-process on loopback UDP so the
E wiring is verifiable locally without hardware.

Honest framing (design v4 §5.5): this module *builds* the physical topology;
the claims it can support are constrained by the ``physical_publishable``
gate (>= 8 attested hosts, deployed-commit markers, ...). A loopback run is a
mechanism check, not a deployment measurement.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import socket
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel

from PiCN.Layers.LinkLayer.Interfaces import UDP4Interface
from PiCN.Layers.PacketEncodingLayer.Encoder import NdnTlvEncoder
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

from agentic.adapters.picn import PicnSubstratePort
from agentic.agentic_layer import AgenticLayer, RecordingLatencyObserver
from agentic.agentic_layer.decomposer import SubIntent
from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.binding import DeterministicBackend, LatencyBackend
from agentic.binding.pydantic_ai_backend import PydanticAIBackend
from agentic.port.events import SubstrateEvent
from agentic.port.names import EndpointRef, Match, Name, PrefixTable
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact

from demo.concurrency_topology import INPUT_SCHEMA, OUTPUT_SCHEMA

PHYSICAL_TRANSPORT = "udp"

# Leaf capability naming: /cap/fwd/hospital/beds/h1 .. h{k} (design v4 §5.1).
_CAPABILITY_BASE: tuple[str, ...] = ("cap", "fwd", "hospital", "beds")
_EDGE_HOST_LOOPBACK = "127.0.0.1"
_SERVED_BY_LOOPBACK = "localhost"


def leaf_name_str(index: int) -> str:
    """Wire name of leaf ``index`` (0-based): ``/cap/fwd/hospital/beds/h<1-based>``."""
    return "/".join(("",) + _CAPABILITY_BASE + (f"h{index + 1}",))


def _leaf_wire_name(index: int) -> Name:
    return Name(tuple(part.encode("utf-8") for part in _CAPABILITY_BASE) + (f"h{index + 1}".encode(),))


def split_leaves(k: int, edges: int) -> list[tuple[str, list[int]]]:
    """Split ``k`` leaves contiguously over ``edges`` edges (>= 1).

    :return: ``[(edge_id, [leaf_index, ...]), ...]`` with ``edge_id`` =
        ``edge1`` .. ``edgeN``; later edges get the remainder.
    :raises ValueError: When ``edges < 1`` or ``k < 1``.
    """
    if edges < 1:
        raise ValueError(f"edges must be >= 1, got {edges}")
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    base, remainder = divmod(k, edges)
    split: list[tuple[str, list[int]]] = []
    next_index = 0
    for edge_number in range(1, edges + 1):
        count = base + (1 if edge_number <= remainder else 0)
        split.append((f"edge{edge_number}", list(range(next_index, next_index + count))))
        next_index += count
    return split


@dataclass
class PhysicalLeafOutcome:
    """Per-leaf measurement seen from the intake and producer records.

    :param leaf_index: 0-based leaf index.
    :param name: Wire name of the leaf capability.
    :param edge_id: Edge the leaf was dispatched to (prefix owner).
    :param served_by_host: Host of the answering node (producer clock domain).
    :param inference_ms: Producer-side measured model-inference wall-clock
        (LLM backend only; ``None`` for deterministic leaves — never
        attributed to the overlay).
    :param transport_ms: Intake-clock ``t_response - t_send`` for the leaf.
    :param service_ms: Producer-side measured service wall-clock
        (clock-offset-corrected), including any deterministic sleep.
    """

    leaf_index: int
    name: str
    edge_id: str
    served_by_host: str
    inference_ms: float | None
    transport_ms: float | None
    service_ms: float | None


@dataclass
class PhysicalRunResult:
    """Outcome of one physical-cell run (single intent fan-out)."""

    run_id: str
    seed: int
    k: int
    edges: int
    backend: str
    llm_placement: str | None
    leaf_latency_s: float | None
    transport: str
    dispatch: str
    elapsed_ms: float
    trace_root_hex: str
    trace_root_verified: bool
    t_decompose_ms: float | None
    t_dispatch_ms: float | None
    t_network_ms: float | None
    t_service_ms: float | None
    t_aggregate_ms: float | None
    t_intent_ms: float | None
    leaves: list[PhysicalLeafOutcome]
    per_edge_leaf_counts: dict[str, int]
    clock_offset_ms: float
    hosts: list[str] = field(default_factory=list)


class MultiEdgePort:
    """``SubstratePort`` facade over one ``PicnSubstratePort`` per edge.

    The intake ``AgenticLayer`` sees a single port; ``send_request`` routes
    each leaf to the edge whose declared capability prefixes directionally
    LPM-match the leaf name (the paper's demonstrated routing mechanism,
    matching PR-2). Per-edge leaf counts are recorded for the gate's
    multi-edge breadth condition (NF-5).
    """

    def __init__(
        self,
        edge_owned: dict[str, list[str]],
        ports: dict[str, PicnSubstratePort],
        served_by_host: dict[str, str] | None = None,
    ) -> None:
        if not edge_owned or set(edge_owned) != set(ports):
            raise ValueError("every edge needs owned prefixes and a port")
        self.edge_owned = edge_owned
        self._ports = ports
        self._primary = next(iter(ports.values()))
        # Routing table: Name prefix -> edge id (longest match wins). Name
        # components are bytes — the wire name space (see agentic.port.names).
        self._table: list[tuple[Name, str]] = []
        for edge_id, owned in edge_owned.items():
            for prefix in owned:
                self._table.append(
                    (
                        Name(tuple(part.encode("utf-8") for part in prefix.strip("/").split("/"))),
                        edge_id,
                    )
                )
        self._leaf_counts: dict[str, int] = {edge_id: 0 for edge_id in ports}
        self._correlation_edge: dict[bytes, str] = {}
        self._served_by_host = served_by_host or {}

    @property
    def leaf_counts(self) -> dict[str, int]:
        return dict(self._leaf_counts)

    def route(self, name: Name) -> str:
        """Return the edge id whose owned prefix directionally LPM-matches."""
        best: tuple[int, str] | None = None
        for prefix, edge_id in self._table:
            if prefix.is_prefix_of(name):
                if best is None or len(prefix) > best[0]:
                    best = (len(prefix), edge_id)
        if best is None:
            raise ValueError(f"no edge owns capability prefix of {name}")
        return best[1]

    # --- SubstratePort protocol -------------------------------------------

    async def start(self, inbound: asyncio.Queue[SubstrateEvent]) -> None:
        for port in self._ports.values():
            await port.start(inbound)

    async def stop(self) -> None:
        for port in self._ports.values():
            await port.stop()

    def parse_name(self, s: str) -> Name:
        return self._primary.parse_name(s)

    def name_from_components(self, parts: Any) -> Name:
        return self._primary.name_from_components(parts)

    def longest_prefix_match(self, name: Name, table: PrefixTable) -> Match | None:
        return self._primary.longest_prefix_match(name, table)

    async def register_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        await self._primary.register_prefix(prefix, endpoint)

    async def unregister_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        await self._primary.unregister_prefix(prefix, endpoint)

    def lookup(self, name: Name) -> Any:
        return self._primary.lookup(name)

    async def send_request(
        self,
        name: Name,
        payload: bytes,
        *,
        correlation: bytes,
        deadline: float,
    ) -> None:
        edge_id = self.route(name)
        self._leaf_counts[edge_id] = self._leaf_counts.get(edge_id, 0) + 1
        self._correlation_edge[correlation] = edge_id
        await self._ports[edge_id].send_request(
            name, payload, correlation=correlation, deadline=deadline
        )

    async def send_response(self, correlation: bytes, payload: bytes) -> None:
        edge_id = self._correlation_edge.get(correlation)
        port = self._ports[edge_id] if edge_id is not None else self._primary
        await port.send_response(correlation, payload)


class _BedsInput(BaseModel):
    """Input schema for the hospital-beds LLM producer (demo payload)."""

    patient_id: str = ""


class _BedsOutput(BaseModel):
    """Output schema for the hospital-beds LLM producer."""

    beds_free: int


def _descriptor_for(
    name: Name,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
) -> Any:
    """Signed capability descriptor with backend-matching schemas."""
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
        "input_schema": input_schema,
        "output_schema": output_schema,
        "freshness_bound_s": 60,
        "revocation_pointer": "none",
    }
    desc = parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)),
        capability_path=(b"hospital", b"beds"),
    )
    return dataclasses.replace(desc, name=name)


def _make_backend(
    *,
    backend: str,
    leaf_index: int,
    leaf_latency_s: float | None,
    model_config: str | None,
    llm_placement: str | None,
) -> tuple[LatencyBackend, dict[str, Any], dict[str, Any]]:
    """Build the leaf producer: deterministic (optionally latency-wrapped) or LLM.

    The ``LatencyBackend`` wrapper is always the outermost layer so the
    producer-side service wall-clock is genuinely measured (inference included).
    Returns ``(backend, input_schema, output_schema)`` — the descriptor must
    declare the backend's own schemas so registration compatibility holds.
    """
    if backend == "llm":
        output: dict[str, Any] = {"beds_free": 10 + leaf_index}
        inner: Any = PydanticAIBackend(
            input_model=_BedsInput,
            output_model=_BedsOutput,
            model_config_path=model_config,
            test_output=output,
        )
        input_schema, output_schema = inner.declared_schemas()
        # LLM cells drop --leaf-latency-s (Finding 9): inference dominates, and
        # the measured invoke wall-clock IS the inference time.
        return LatencyBackend(inner, latency_s=0.0), input_schema, output_schema
    deterministic = DeterministicBackend(
        lambda payload, _i=leaf_index: {"beds_free": 10 + _i},
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
    )
    return (
        LatencyBackend(deterministic, latency_s=leaf_latency_s or 0.0),
        INPUT_SCHEMA,
        OUTPUT_SCHEMA,
    )


@dataclass
class _PhysicalTopologyHandle:
    """Started topology: intake layer, multi-edge port, edge forwarders."""

    layer: AgenticLayer
    port: MultiEdgePort
    edges: dict[str, AgenticForwarder]
    observer: RecordingLatencyObserver | None
    leaf_backends: dict[int, LatencyBackend]
    edge_of_leaf: dict[int, str]
    edge_hosts: dict[str, str]

    async def stop(self) -> None:
        try:
            self.layer._started_port = False
            if self.layer._event_task is not None:
                self.layer._event_task.cancel()
        except Exception:
            pass
        await self.port.stop()
        for forwarder in self.edges.values():
            try:
                await asyncio.wait_for(
                    asyncio.shield(forwarder.stop_forwarder_async()), timeout=1.0
                )
            except Exception:
                pass


async def build_physical_topology(
    *,
    k: int,
    edges: int,
    backend: str = "deterministic",
    llm_placement: str | None = None,
    leaf_latency_s: float | None = None,
    model_config: str | None = None,
    edge_endpoints: list[tuple[str, int]] | None = None,
    observer_on: bool = True,
    log_level: int = 255,
) -> _PhysicalTopologyHandle:
    """Wire the Experiment E topology; the caller owns starting and stopping.

    :param edge_endpoints: Optional ``[(host, udp_port), ...]`` per edge. When
        absent, edges are built in-process on ephemeral loopback UDP ports
        (the no-hardware verification mode).
    """
    if edges < 1:
        raise ValueError(f"edges must be >= 1, got {edges}")
    encoder = NdnTlvEncoder()
    split = split_leaves(k, edges)

    forwarders: dict[str, AgenticForwarder] = {}
    edge_endpoints_resolved: list[tuple[str, int]] = []
    edge_owned: dict[str, list[str]] = {}
    leaf_backends: dict[int, LatencyBackend] = {}
    edge_of_leaf: dict[int, str] = {}
    leaf_owner_host: dict[int, str] = {}

    for edge_number, (edge_id, leaf_indices) in enumerate(split):
        if edge_endpoints is None:
            interface = UDP4Interface(0)
            edge = AgenticForwarder(
                port=0,
                encoder=encoder,
                interfaces=[interface],
                log_level=log_level,
                ageing_interval=1,
                runtime=Runtime.ASYNC,
            )
            edge_endpoints_resolved.append((_EDGE_HOST_LOOPBACK, interface.get_port()))
        else:
            host, port_number = edge_endpoints[edge_number]
            edge_endpoints_resolved.append((host, port_number))
            edge = AgenticForwarder(
                port=port_number,
                encoder=encoder,
                log_level=log_level,
                ageing_interval=1,
                runtime=Runtime.ASYNC,
            )
        forwarders[edge_id] = edge
        owned: list[str] = []
        for leaf_index in leaf_indices:
            wire_name = _leaf_wire_name(leaf_index)
            leaf_backend, input_schema, output_schema = _make_backend(
                backend=backend,
                leaf_index=leaf_index,
                leaf_latency_s=leaf_latency_s,
                model_config=model_config,
                llm_placement=llm_placement,
            )
            desc = _descriptor_for(wire_name, input_schema, output_schema)
            edge.register_capability(desc, leaf_backend, backend_label=backend)
            leaf_backends[leaf_index] = leaf_backend
            edge_of_leaf[leaf_index] = edge_id
            leaf_owner_host[leaf_index] = edge_endpoints_resolved[-1][0]
            owned.append(leaf_name_str(leaf_index))
        edge_owned[edge_id] = owned

    # One PicnSubstratePort per edge (real-UDP branch: peer_host/peer_port).
    if edge_endpoints is None:
        # In-process edges: connect through each edge's own bound UDP socket.
        ports = {
            edge_id: PicnSubstratePort(
                edge_endpoints_resolved[i][0],
                edge_endpoints_resolved[i][1],
                encoder=encoder,
                interfaces=[UDP4Interface(0)],
                log_level=log_level,
            )
            for i, (edge_id, _) in enumerate(split)
        }
        served_by_host = {
            edge_id: _SERVED_BY_LOOPBACK for edge_id, _ in split
        }
    else:
        ports = {
            edge_id: PicnSubstratePort(
                edge_endpoints_resolved[i][0],
                edge_endpoints_resolved[i][1],
                encoder=encoder,
                log_level=log_level,
            )
            for i, (edge_id, _) in enumerate(split)
        }
        served_by_host = {
            edge_id: edge_endpoints_resolved[i][0] for i, (edge_id, _) in enumerate(split)
        }

    # Leaf -> answering host: on loopback the answerer is the in-process edge
    # (localhost); remotely, the collector attributes per service records.
    observer = RecordingLatencyObserver() if observer_on else None
    multi = MultiEdgePort(edge_owned, ports, served_by_host=served_by_host)
    layer = AgenticLayer(runtime="async", port=multi, observer=observer)  # type: ignore[arg-type]

    for edge in forwarders.values():
        await edge.start_forwarder_async()
    await layer.start_port()
    await asyncio.sleep(0.05)

    return _PhysicalTopologyHandle(
        layer=layer,
        port=multi,
        edges=forwarders,
        observer=observer,
        leaf_backends=leaf_backends,
        edge_of_leaf=edge_of_leaf,
        edge_hosts=dict(served_by_host),
    )


async def run_physical_cell(
    *,
    run_id: str,
    seed: int,
    k: int,
    edges: int,
    leaf_latency_s: float | None,
    backend: str,
    llm_placement: str | None,
    model_config: str | None = None,
    edge_endpoints: list[tuple[str, int]] | None = None,
    observer_on: bool = True,
    log_level: int = 255,
) -> PhysicalRunResult:
    """Run one Experiment E cell: concurrent fan-out of one intent over UDP.

    One intent is decomposed into ``k`` leaves; the intake routes each leaf to
    its owning edge over a dedicated ``PicnSubstratePort``; the answering
    producers register their measured service (inference) time producer-side.
    """
    handle = await build_physical_topology(
        k=k,
        edges=edges,
        backend=backend,
        llm_placement=llm_placement,
        leaf_latency_s=leaf_latency_s,
        model_config=model_config,
        edge_endpoints=edge_endpoints,
        observer_on=observer_on,
        log_level=log_level,
    )
    layer = handle.layer
    observer = handle.observer
    result: PhysicalRunResult | None = None
    try:
        await asyncio.sleep(0.05)

        issuer = generate_ed25519_private_key()
        issuer_der = issuer.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        parent = hashlib.sha256(
            f"physical-{run_id}-{seed}-{k}-{edges}".encode()
        ).digest()

        subs = [
            SubIntent(index=i, capability=(*_CAPABILITY_BASE[2:], f"h{i + 1}"), bindings={})
            for i in range(k)
        ]

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
                dispatch="concurrent",
                emission_ts=emission_ts if observer_on else None,
            ),
            timeout=30.0,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        # T_intent decomposition (intake single clock, as in D).
        t_commit = (
            observer.commits[parent][0] if observer and parent in observer.commits else None
        )
        t0 = observer.dispatch_begin.get(parent) if observer else None
        t1 = observer.dispatch_end.get(parent) if observer else None
        t_agg = observer.aggregate.get(parent)[0] if observer and parent in observer.aggregate else None
        t_intent = observer.complete.get(parent) if observer else None
        t_complete = t_intent if t_intent is not None else time.perf_counter()

        t_decompose_ms = (
            (t_commit - emission_ts) * 1000.0
            if t_commit is not None and emission_ts is not None
            else None
        )
        t_dispatch_ms = (t1 - t0) * 1000.0 if t0 is not None and t1 is not None else None
        t_aggregate_ms = (t_complete - t1) * 1000.0 if t1 is not None else None
        t_intent_ms = (
            (t_complete - emission_ts) * 1000.0 if emission_ts is not None else None
        )

        # T_network (intake clock both ends): mean t_response - t_send.
        network_vals: list[float] = []
        intervals: list[tuple[int, float, float]] = []
        if observer:
            for (p, leaf_index), t_resp in observer.leaf_response.items():
                if p != parent:
                    continue
                fwd = observer.leaf_forwarded.get((p, leaf_index))
                if fwd is not None:
                    network_vals.append(t_resp - fwd[1])
                    intervals.append((leaf_index, fwd[1], t_resp))
        t_network_ms = (
            (sum(network_vals) / len(network_vals)) * 1000.0 if network_vals else None
        )

        # Per-leaf outcomes: transport split from the intake observer,
        # service/inference from the producer-side measured records.
        measured_service: dict[int, float] = {}
        for leaf_index, backend_obj in handle.leaf_backends.items():
            if backend_obj.last_measured_s is not None:
                measured_service[leaf_index] = backend_obj.last_measured_s * 1000.0
        # Loopback single clock: producer-side offset correction is 0 by
        # construction; a deployment applies the collector's measured offset.
        clock_offset_ms = 0.0
        inference_vals = [
            measured_service[i]
            for i in sorted(measured_service)
            if backend == "llm"
        ]

        leaves: list[PhysicalLeafOutcome] = []
        if observer:
            for (p, leaf_index), t_resp in observer.leaf_response.items():
                if p != parent:
                    continue
                fwd = observer.leaf_forwarded.get((p, leaf_index))
                transport_ms = (t_resp - fwd[1]) * 1000.0 if fwd is not None else None
                service_ms = measured_service.get(leaf_index)
                edge_id = handle.edge_of_leaf[leaf_index]
                leaves.append(
                    PhysicalLeafOutcome(
                        leaf_index=leaf_index,
                        name=leaf_name_str(leaf_index),
                        edge_id=edge_id,
                        served_by_host=handle.edge_hosts.get(edge_id, _SERVED_BY_LOOPBACK),
                        inference_ms=service_ms if backend == "llm" else None,
                        transport_ms=transport_ms,
                        service_ms=service_ms,
                    )
                )
            leaves.sort(key=lambda leaf: leaf.leaf_index)

        per_edge: dict[str, int] = {}
        for leaf in leaves:
            per_edge[leaf.edge_id] = per_edge.get(leaf.edge_id, 0) + 1
        if not per_edge:
            per_edge = dict(handle.port.leaf_counts)

        inferred_t_service = (
            (sum(inference_vals) / len(inference_vals)) if inference_vals else None
        )

        result = PhysicalRunResult(
            run_id=run_id,
            seed=seed,
            k=k,
            edges=edges,
            backend=backend,
            llm_placement=llm_placement,
            leaf_latency_s=leaf_latency_s,
            transport=PHYSICAL_TRANSPORT,
            dispatch="concurrent",
            elapsed_ms=elapsed_ms,
            trace_root_hex=root.hex(),
            trace_root_verified=(
                layer.pit.get(parent) is not None and layer.pit.get(parent).terminated  # type: ignore[union-attr]
            ),
            t_decompose_ms=t_decompose_ms,
            t_dispatch_ms=t_dispatch_ms,
            t_network_ms=t_network_ms,
            t_service_ms=inferred_t_service if backend == "llm" else (
                (sum(measured_service.values()) / len(measured_service))
                if measured_service
                else None
            ),
            t_aggregate_ms=t_aggregate_ms,
            t_intent_ms=t_intent_ms,
            leaves=leaves,
            per_edge_leaf_counts=per_edge,
            clock_offset_ms=clock_offset_ms,
            hosts=[socket.gethostname()],
        )
    finally:
        await handle.stop()

    if result is None:
        raise RuntimeError("physical cell run failed before producing a result")
    return result


__all__ = [
    "PHYSICAL_TRANSPORT",
    "MultiEdgePort",
    "PhysicalLeafOutcome",
    "PhysicalRunResult",
    "build_physical_topology",
    "leaf_name_str",
    "run_physical_cell",
    "split_leaves",
]
