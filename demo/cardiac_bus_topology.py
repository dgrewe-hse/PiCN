# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Paper-aligned cardiac network demo on PiCN SimulationBus.

Ambulance ``AgenticLayer.submit_intent`` emits capability Interests
(``/cap/fwd/...``) that travel the bus to an edge ``AgenticForwarder``.
Hospital capacity is published as Content under distinct capability names
(``/cap/fwd/hospital/beds/h0``, …). Aggregation yields a Context~PIT Merkle
trace root at the ambulance — matching the ComMag §usecase wire story
(capability names, not NFN lambdas).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from PiCN.Layers.LinkLayer.Interfaces import SimulationBus
from PiCN.Layers.PacketEncodingLayer.Encoder import NdnTlvEncoder
from PiCN.Mgmt import MgmtClient
from PiCN.Packets import Name as PicnName
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

from agentic.adapters.picn import PicnSubstratePort
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.decomposer import SubIntent
from agentic.port.events import ResponseArrived, SubstrateEvent
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key

from demo.bus_topology import _mgmt_port, unblock_sim_interfaces


class RecordingPicnSubstratePort(PicnSubstratePort):
    """``PicnSubstratePort`` that records outbound Interests and Content payloads."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.recorded_requests: list[tuple[Name, bytes]] = []
        self.recorded_responses: dict[bytes, bytes] = {}

    async def send_request(
        self,
        name: Name,
        payload: bytes,
        *,
        correlation: bytes,
        deadline: float,
    ) -> None:
        self.recorded_requests.append((name, correlation))
        await super().send_request(
            name, payload, correlation=correlation, deadline=deadline
        )

    async def _put(self, event: SubstrateEvent) -> None:
        if isinstance(event, ResponseArrived):
            self.recorded_responses[event.correlation] = event.payload
        await super()._put(event)


def _hospital_id(index: int) -> str:
    return f"h{index}"


def _cap_name(*parts: str) -> PicnName:
    name = PicnName("/cap/fwd")
    for part in parts:
        name += part
    return name


def _wire_name(*parts: str) -> Name:
    return Name((b"cap", b"fwd") + tuple(p.encode("utf-8") for p in parts))


def _hospital_payload(
    *,
    hospital_id: str,
    beds_free: int,
    paediatric_team: bool,
) -> str:
    body = {
        "hospital_id": hospital_id,
        "beds_free": beds_free,
        "paediatric_team": paediatric_team,
    }
    return json.dumps(body, separators=(",", ":"), sort_keys=True)


@dataclass
class CardiacBusResult:
    """Outcome of one ambulance → edge SimulationBus exchange.

    :param seed: Run seed.
    :param k: Hospital count (distinct capability Content names).
    :param elapsed_ms: Wall-clock latency of ``submit_intent``.
    :param trace_root_hex: Aggregated Context~PIT Merkle root.
    :param sub_intent_names: Capability Interest names that left the ambulance.
    :param hospital_answers: Parsed hospital Content payloads.
    :param ranked: Hospitals ranked for intake (paediatric filter + beds).
    :param chosen_hospital_id: Top-ranked hospital after filters.
    :param wire_bytes_estimate: Approximate Interest name bytes.
    :param simulated_interfaces: Faces on the bus.
    :param dispatch_count: Sub-intents forwarded.
    :param context_pit_peak: Peak Context~PIT entries (1 parent).
    """

    seed: int
    k: int
    elapsed_ms: float
    trace_root_hex: str
    sub_intent_names: list[str]
    hospital_answers: list[dict[str, Any]]
    ranked: list[dict[str, Any]]
    chosen_hospital_id: str | None
    wire_bytes_estimate: int
    simulated_interfaces: int
    dispatch_count: int
    context_pit_peak: int
    require_paediatric: bool = True
    extras: dict[str, Any] = field(default_factory=dict)


def _rank_hospitals(
    answers: list[dict[str, Any]],
    *,
    require_paediatric: bool,
) -> list[dict[str, Any]]:
    """Rank by paediatric availability (if required), then beds_free descending."""
    eligible = []
    for row in answers:
        if require_paediatric and not row.get("paediatric_team"):
            continue
        eligible.append(dict(row))
    eligible.sort(key=lambda r: int(r.get("beds_free", 0)), reverse=True)
    return eligible


def _configure_edge_cs(
    edge_mgmt_port: int,
    *,
    hospital_payloads: list[str],
    enrichment: str,
    traffic: str,
    ranking: str,
) -> None:
    """Install capability Content on the edge node (do not shutdown the relay)."""
    client = MgmtClient(edge_mgmt_port)
    client.add_new_content(_cap_name("enrichment"), enrichment)
    client.add_new_content(_cap_name("traffic"), traffic)
    client.add_new_content(_cap_name("ranking"), ranking)
    for index, payload in enumerate(hospital_payloads):
        client.add_new_content(
            _cap_name("hospital", "beds", _hospital_id(index)), payload
        )


async def _safe_stop(awaitable: Any, *, timeout: float = 1.0) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(awaitable), timeout=timeout)
    except Exception:
        pass


async def run_cardiac_bus(
    *,
    k: int = 2,
    seed: int = 1,
    require_paediatric: bool = True,
    log_level: int = 255,
    adversary_index: int | None = None,
) -> CardiacBusResult:
    """Run the paper cardiac narrative on SimulationBus.

    Topology (2 faces)::

        ambulance (PicnSubstratePort / AgenticLayer.submit_intent)
            → edge (AgenticForwarder CS under /cap/fwd/…)

    Hospital agents are modelled as distinct capability Content names
    (``hospital/beds/h0`` …) answered at the edge. Interests and Content
    traverse the SimulationBus; wire names are ``/cap/fwd/...``, not NFN.

    :param k: Number of hospital capability names (≥ 2).
    :param seed: Deterministic seed for bed counts / paediatric flags.
    :param require_paediatric: Filter ranking to paediatric-capable hospitals.
    :param log_level: PiCN logger level.
    :param adversary_index: Optional hospital that advertises inflated beds.
    """
    if k < 2:
        raise ValueError("k must be >= 2")

    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder, log_level=255)
    tag = f"{seed}-{k}-{time.time_ns() % 1_000_000}"
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

    hospital_payloads: list[str] = []
    hospital_meta: list[dict[str, Any]] = []
    for i in range(k):
        true_beds = 2 + i + (seed % 3)
        paediatric = (i + seed) % 2 == 0 or i == 0
        advertised = true_beds
        if adversary_index is not None and i == adversary_index:
            advertised = true_beds + 10
        hid = _hospital_id(i)
        payload = _hospital_payload(
            hospital_id=hid,
            beds_free=advertised,
            paediatric_team=paediatric,
        )
        hospital_payloads.append(payload)
        hospital_meta.append(
            {
                "hospital_id": hid,
                "beds_free": advertised,
                "true_beds": true_beds,
                "paediatric_team": paediatric,
                "adversarial": adversary_index is not None and i == adversary_index,
            }
        )

    enrichment = json.dumps(
        {
            "status": "enriched",
            "ecg_hash": hashlib.sha256(f"ecg-{seed}".encode()).hexdigest()[:16],
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    traffic = json.dumps({"eta_s": 120 + seed}, separators=(",", ":"), sort_keys=True)
    ranking = json.dumps(
        {"policy": "beds_then_paediatric"}, separators=(",", ":"), sort_keys=True
    )

    port = RecordingPicnSubstratePort(
        edge_addr,
        None,
        encoder=encoder,
        interfaces=[bus.add_interface(amb_addr)],
        log_level=log_level,
    )
    layer = AgenticLayer(runtime="async", port=port, log_level=log_level)

    result: CardiacBusResult | None = None
    await edge.start_forwarder_async()
    bus.start_process()
    await layer.start_port()
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: _configure_edge_cs(
                    _mgmt_port(edge),
                    hospital_payloads=hospital_payloads,
                    enrichment=enrichment,
                    traffic=traffic,
                    ranking=ranking,
                ),
            ),
            timeout=5.0,
        )
        await asyncio.sleep(0.05)

        issuer = generate_ed25519_private_key()
        issuer_der = issuer.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        parent = hashlib.sha256(f"cardiac-bus-{seed}-{k}".encode()).digest()

        subs: list[SubIntent] = [
            SubIntent(index=0, capability=("enrichment",), bindings={}),
        ]
        for i in range(k):
            hid = _hospital_id(i)
            subs.append(
                SubIntent(
                    index=1 + i,
                    capability=("hospital", "beds", hid),
                    bindings={"hospital_id": hid},
                )
            )
        subs.append(SubIntent(index=1 + k, capability=("traffic",), bindings={}))
        subs.append(SubIntent(index=2 + k, capability=("ranking",), bindings={}))

        wire_est = sum(
            sum(len(c) for c in _wire_name(*sub.capability).components) + 16
            for sub in subs
        )

        started = time.perf_counter()
        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=issuer_der,
                sub_intents=subs,
                aggregation_policy="ALL",
                payload=b"{}",
                deadline=time.monotonic() + 10.0,
                now_ms=seed * 1000,
            ),
            timeout=12.0,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        hospital_answers: list[dict[str, Any]] = []
        for i in range(k):
            hid = _hospital_id(i)
            digest = hashlib.sha256(
                parent
                + bytes(str(1 + i), "ascii")
                + b"/".join(c.encode() for c in ("hospital", "beds", hid))
            ).digest()
            corr = hashlib.sha256(parent + digest).digest()
            raw = port.recorded_responses.get(corr, b"")
            if raw:
                try:
                    hospital_answers.append(json.loads(raw.decode()))
                except json.JSONDecodeError:
                    hospital_answers.append(
                        {"hospital_id": hid, "raw": raw.decode(errors="replace")}
                    )
            else:
                hospital_answers.append(dict(hospital_meta[i]))

        ranked = _rank_hospitals(
            hospital_answers, require_paediatric=require_paediatric
        )
        chosen = ranked[0]["hospital_id"] if ranked else None
        entry = layer.pit.get(parent)
        name_strs = [
            "/"
            + "/".join(c.decode("utf-8", errors="replace") for c in n.components)
            for n, _ in port.recorded_requests
        ]

        result = CardiacBusResult(
            seed=seed,
            k=k,
            elapsed_ms=elapsed_ms,
            trace_root_hex=root.hex(),
            sub_intent_names=name_strs,
            hospital_answers=hospital_answers,
            ranked=ranked,
            chosen_hospital_id=chosen,
            wire_bytes_estimate=wire_est,
            simulated_interfaces=2,
            dispatch_count=len(subs),
            context_pit_peak=1 if entry is not None else 0,
            require_paediatric=require_paediatric,
            extras={
                "hospital_meta": hospital_meta,
                "trace_root_verified": entry is not None and entry.terminated,
                "aggregation_complete": (
                    1 if entry is not None and entry.terminated else 0
                ),
                "workload": "cardiac_capability_bus",
                "topology": "ambulance_port_to_edge_agentic_forwarder",
            },
        )
    finally:
        # Wake SimulationInterface pumps BEFORE stopping stacks (blocking get()).
        unblock_sim_interfaces(edge, port)
        await asyncio.sleep(0.05)
        # Best-effort teardown; never block the demo return path.
        try:
            layer._started_port = False
            if layer._event_task is not None:
                layer._event_task.cancel()
        except Exception:
            pass
        try:
            for iface in list(getattr(port, "interfaces", []) or []):
                try:
                    iface.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            await _safe_stop(edge.stop_forwarder_async(), timeout=1.0)
        except Exception:
            pass
        try:
            bus.stop_process()
        except Exception:
            pass

    if result is None:
        raise RuntimeError("cardiac bus run failed before producing a result")
    return result


__all__ = [
    "CardiacBusResult",
    "RecordingPicnSubstratePort",
    "run_cardiac_bus",
]
