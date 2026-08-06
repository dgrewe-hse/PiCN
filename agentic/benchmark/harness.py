# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Orchestrate scenario runs under A-011 gates and record raw events."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic.benchmark.capacity import assert_bus_capacity
from agentic.benchmark.errors import HarnessError
from agentic.benchmark.events import MetricEvent
from agentic.benchmark.metadata import (
    RunMetadata,
    require_run_metadata,
    working_tree_dirty,
)
from agentic.benchmark.metrics import MetricsSnapshot, compute_metrics
from agentic.benchmark.rollup import rollup_by_transport
from agentic.scenario import available_scenarios


@dataclass
class RunConfig:
    """Inputs for one harness run.

    :param seed: Required RNG / scenario seed.
    :param transport: Required ``bus`` or ``udp``.
    :param k: Scenario scale (hospital count).
    :param scenario_name: Registered scenario plugin name.
    :param allow_dirty: Permit dirty trees; stamps non-reproducible.
    :param executor_workers: Shared executor size (bus capacity check).
    :param simulated_interfaces: Interfaces that would hold an executor worker.
    :param path: ``happy`` or ``adversary``.
    """

    seed: int | None
    transport: str | None
    k: int = 3
    scenario_name: str = "cardiac-response"
    allow_dirty: bool = False
    executor_workers: int = 8
    simulated_interfaces: int = 0
    path: str = "happy"


def _metrics_dict(metrics: MetricsSnapshot) -> dict[str, Any]:
    return {
        "transport": metrics.transport,
        "m1_latency_ms": metrics.m1_latency_ms,
        "m2_message_count": metrics.m2_message_count,
        "m2_message_bytes": metrics.m2_message_bytes,
        "m3_overhead_ratio": metrics.m3_overhead_ratio,
        "m3_publishable": metrics.m3_publishable,
        # Paper-facing aliases (NFN combine stack overhead; not cardiac-over-bus).
        "nfn_stack_overhead_ratio": metrics.nfn_stack_overhead_ratio,
        "nfn_stack_overhead_publishable": metrics.nfn_stack_overhead_publishable,
        "m4_detection_rate": metrics.m4_detection_rate,
        "m5_scale_latency_by_k": metrics.m5_scale_latency_by_k,
        "context_pit_peak": metrics.context_pit_peak,
        "dispatch_count": metrics.dispatch_count,
        "aggregation_complete": metrics.aggregation_complete,
        "artefact_bytes_total": metrics.artefact_bytes_total,
    }


@dataclass
class MeasurementHarness:
    """Run a scenario under metadata/capacity gates; emit raw events + rollups."""

    repo_root: Path | None = None
    events: list[MetricEvent] = field(default_factory=list)
    last_metadata: RunMetadata | None = None
    last_metrics: MetricsSnapshot | None = None

    def _cwd(self) -> str | None:
        return str(self.repo_root) if self.repo_root is not None else None

    def prepare(self, config: RunConfig) -> RunMetadata:
        """Validate gates and return run metadata (does not execute the scenario)."""
        dirty = working_tree_dirty(cwd=self._cwd())
        metadata = require_run_metadata(
            seed=config.seed,
            transport=config.transport,
            k=config.k,
            allow_dirty=config.allow_dirty,
            dirty=dirty,
            parameters={"path": config.path, "scenario": config.scenario_name},
        )
        assert_bus_capacity(
            transport=metadata.transport,
            executor_workers=config.executor_workers,
            simulated_interfaces=config.simulated_interfaces,
        )
        self.last_metadata = metadata
        return metadata

    def _emit_structural(
        self,
        artefacts: dict[str, Any],
        *,
        transport: str,
        seed: int,
        path: str,
    ) -> None:
        """Map scenario structural artefacts onto MetricEvents."""
        labels = {"path": path}
        peak = artefacts.get("context_pit_peak")
        if peak is not None:
            self.events.append(
                MetricEvent(
                    kind="context_pit_peak",
                    transport=transport,
                    seed=seed,
                    value=float(peak),
                    labels=labels,
                )
            )
        entries = artefacts.get("context_pit_entries")
        if entries is not None:
            self.events.append(
                MetricEvent(
                    kind="context_pit_entries",
                    transport=transport,
                    seed=seed,
                    value=float(entries),
                    labels=labels,
                )
            )
        dispatch = artefacts.get("dispatch_count")
        if dispatch is not None:
            self.events.append(
                MetricEvent(
                    kind="dispatch_count",
                    transport=transport,
                    seed=seed,
                    value=float(dispatch),
                    labels=labels,
                )
            )
        agg = artefacts.get("aggregation_complete")
        if agg is not None:
            self.events.append(
                MetricEvent(
                    kind="aggregation_complete",
                    transport=transport,
                    seed=seed,
                    value=float(agg),
                    labels=labels,
                )
            )
        artefact_bytes = artefacts.get("artefact_bytes")
        if artefact_bytes is not None:
            self.events.append(
                MetricEvent(
                    kind="artefact_bytes",
                    transport=transport,
                    seed=seed,
                    value=float(artefact_bytes),
                    labels=labels,
                )
            )

    async def run(self, config: RunConfig, *, port: Any = None) -> dict[str, Any]:
        """Execute one scenario path and capture measurement events.

        Phase-1 / in-process runs emit agentic latency and structural metrics
        only. They do **not** emit a synthetic NFN baseline — publishable M3
        requires a real plain-NFN paired run (see ``demo.run_paired_bus``).

        :param port: Optional substrate port injected into the scenario.
        :return: Artefacts including metadata and per-transport metrics.
        """
        metadata = self.prepare(config)
        scenarios = available_scenarios()
        cls = scenarios.get(config.scenario_name)
        if cls is None:
            raise HarnessError(f"unknown scenario: {config.scenario_name!r}")
        scenario = cls(k=config.k, seed=metadata.seed)
        await scenario.setup(port=port, k=config.k)

        started = time.perf_counter()
        try:
            if config.path == "adversary":
                artefacts = await scenario.run_adversary(patient_id="harness-adv")
            else:
                artefacts = await scenario.run_happy_path(patient_id="harness-happy")
        finally:
            await scenario.teardown()
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        transport = metadata.transport
        seed = metadata.seed
        self.events.append(
            MetricEvent(
                kind="latency_sample",
                transport=transport,
                seed=seed,
                value=elapsed_ms,
                labels={"path": config.path},
            )
        )
        msg_count = int(artefacts.get("accountability_log_length", 0))
        if "first_exchange" in artefacts:
            msg_count = int(
                artefacts["first_exchange"].get("accountability_log_length", msg_count)
            )
        per_msg_bytes = float(artefacts.get("artefact_bytes", 0)) / max(msg_count, 1)
        if per_msg_bytes <= 0:
            per_msg_bytes = 64.0
        for _ in range(max(msg_count, 1)):
            self.events.append(
                MetricEvent(
                    kind="message",
                    transport=transport,
                    seed=seed,
                    value=per_msg_bytes,
                    labels={"path": config.path},
                )
            )
        # Agentic latency only — no synthetic nfn_baseline_latency (M3 gate).
        self.events.append(
            MetricEvent(
                kind="agentic_latency",
                transport=transport,
                seed=seed,
                value=elapsed_ms,
                labels={"path": config.path, "mode": "agentic"},
            )
        )
        self._emit_structural(
            artefacts, transport=transport, seed=seed, path=config.path
        )
        if config.path == "adversary":
            mismatches = artefacts.get("kpa_mismatches", [])
            detected = 1.0 if mismatches else 0.0
            self.events.append(
                MetricEvent(
                    kind="detection",
                    transport=transport,
                    seed=seed,
                    value=detected,
                    labels={"adversary": True},
                )
            )
        self.events.append(
            MetricEvent(
                kind="scale_point",
                transport=transport,
                seed=seed,
                value=elapsed_ms,
                labels={"k": config.k},
            )
        )

        metrics = compute_metrics(
            [e for e in self.events if e.transport == transport and e.seed == seed],
            transport=transport,
        )
        self.last_metrics = metrics
        return {
            "metadata": metadata.to_dict(),
            "artefacts": artefacts,
            "metrics": _metrics_dict(metrics),
            "rollups": {
                t: {
                    "m1_latency_ms": snap.m1_latency_ms,
                    "m2_message_count": snap.m2_message_count,
                    "m3_overhead_ratio": snap.m3_overhead_ratio,
                    "m3_publishable": snap.m3_publishable,
                    "m4_detection_rate": snap.m4_detection_rate,
                }
                for t, snap in rollup_by_transport(self.events).items()
            },
        }


__all__ = ["MeasurementHarness", "RunConfig"]
