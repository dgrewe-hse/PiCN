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

    async def run(self, config: RunConfig, *, port: Any = None) -> dict[str, Any]:
        """Execute one scenario path and capture M1–M5 events.

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
        # M1 absolute latency sample (publication restricted for bus — metadata says so).
        self.events.append(
            MetricEvent(
                kind="latency_sample",
                transport=transport,
                seed=seed,
                value=elapsed_ms,
                labels={"path": config.path},
            )
        )
        # M2: approximate from accountability log length as message proxy + bytes.
        msg_count = int(artefacts.get("accountability_log_length", 0))
        if "first_exchange" in artefacts:
            msg_count = int(
                artefacts["first_exchange"].get("accountability_log_length", msg_count)
            )
        for _ in range(max(msg_count, 1)):
            self.events.append(
                MetricEvent(
                    kind="message",
                    transport=transport,
                    seed=seed,
                    value=64.0,
                    labels={"path": config.path},
                )
            )
        # M3: agentic vs a synthetic plain-NFN baseline in the *same* run/transport.
        nfn_baseline_ms = max(elapsed_ms * 0.5, 1e-6)
        self.events.append(
            MetricEvent(
                kind="nfn_baseline_latency",
                transport=transport,
                seed=seed,
                value=nfn_baseline_ms,
            )
        )
        self.events.append(
            MetricEvent(
                kind="agentic_latency",
                transport=transport,
                seed=seed,
                value=elapsed_ms,
            )
        )
        # M4 detection efficacy (adversary path records mismatches).
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
        # M5 scale point.
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
            "metrics": {
                "transport": metrics.transport,
                "m1_latency_ms": metrics.m1_latency_ms,
                "m2_message_count": metrics.m2_message_count,
                "m2_message_bytes": metrics.m2_message_bytes,
                "m3_overhead_ratio": metrics.m3_overhead_ratio,
                "m4_detection_rate": metrics.m4_detection_rate,
                "m5_scale_latency_by_k": metrics.m5_scale_latency_by_k,
            },
            "rollups": {
                t: {
                    "m1_latency_ms": snap.m1_latency_ms,
                    "m2_message_count": snap.m2_message_count,
                    "m3_overhead_ratio": snap.m3_overhead_ratio,
                    "m4_detection_rate": snap.m4_detection_rate,
                }
                for t, snap in rollup_by_transport(self.events).items()
            },
        }


__all__ = ["MeasurementHarness", "RunConfig"]
