# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Bus executor ceiling guard (A-011)."""

from __future__ import annotations

import pytest

from agentic.benchmark import HarnessError, MeasurementHarness, RunConfig
from agentic.benchmark.capacity import assert_bus_capacity
from agentic.benchmark.events import MetricEvent
from agentic.benchmark.rollup import rollup_by_transport


def test_bus_over_capacity_refuses_naming_a011() -> None:
    with pytest.raises(HarnessError, match="A-011"):
        assert_bus_capacity(
            transport="bus",
            executor_workers=4,
            simulated_interfaces=4,
        )


def test_bus_capacity_ok_when_workers_exceed_interfaces() -> None:
    assert_bus_capacity(
        transport="bus",
        executor_workers=5,
        simulated_interfaces=4,
    )


def test_udp_skips_executor_ceiling() -> None:
    # UDP has no SimulationInterface pump — guard is a no-op.
    assert_bus_capacity(
        transport="udp",
        executor_workers=1,
        simulated_interfaces=100,
    )


def test_harness_prepare_refuses_over_capacity() -> None:
    harness = MeasurementHarness()
    with pytest.raises(HarnessError, match="A-011"):
        harness.prepare(
            RunConfig(
                seed=1,
                transport="bus",
                allow_dirty=True,
                executor_workers=2,
                simulated_interfaces=2,
            )
        )


def test_rollups_group_by_transport_never_mixed() -> None:
    events = [
        MetricEvent(kind="latency_sample", transport="bus", seed=1, value=10.0),
        MetricEvent(kind="latency_sample", transport="udp", seed=1, value=20.0),
        MetricEvent(kind="message", transport="bus", seed=1, value=8.0),
        MetricEvent(kind="message", transport="udp", seed=1, value=16.0),
    ]
    rollups = rollup_by_transport(events)
    assert set(rollups) == {"bus", "udp"}
    assert rollups["bus"].m1_latency_ms == 10.0
    assert rollups["udp"].m1_latency_ms == 20.0
    assert rollups["bus"].m2_message_count == 1
    assert rollups["udp"].m2_message_count == 1


def test_groupby_keys_always_include_transport() -> None:
    """Verification companion to A-011: no transport-free grouping in rollups."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "benchmark"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "groupby" in text and "transport" not in text:
            offenders.append(str(path))
    assert offenders == []


@pytest.mark.asyncio
async def test_harness_run_cardiac_happy_on_bus() -> None:
    harness = MeasurementHarness()
    result = await harness.run(
        RunConfig(
            seed=42,
            transport="bus",
            k=2,
            allow_dirty=True,
            executor_workers=8,
            simulated_interfaces=3,
            path="happy",
        )
    )
    assert result["metadata"]["transport"] == "bus"
    assert result["metadata"]["seed"] == 42
    assert result["metrics"]["m3_overhead_ratio"] is not None
    assert result["metrics"]["m2_message_count"] >= 1
    assert "bus" in result["rollups"]


@pytest.mark.asyncio
async def test_harness_run_adversary_records_m4() -> None:
    harness = MeasurementHarness()
    result = await harness.run(
        RunConfig(
            seed=3,
            transport="udp",
            k=2,
            allow_dirty=True,
            path="adversary",
        )
    )
    assert result["metrics"]["m4_detection_rate"] == 1.0
    assert result["artefacts"]["path"] == "adversary"


def test_compute_metrics_rejects_mixed_transport() -> None:
    from agentic.benchmark.metrics import compute_metrics

    events = [
        MetricEvent(kind="latency_sample", transport="bus", seed=1, value=1.0),
        MetricEvent(kind="latency_sample", transport="udp", seed=1, value=2.0),
    ]
    with pytest.raises(HarnessError, match="mixed"):
        compute_metrics(events, transport="bus")


def test_invalid_transport_refused() -> None:
    from agentic.benchmark.metadata import require_run_metadata

    with pytest.raises(HarnessError, match="transport must"):
        require_run_metadata(
            seed=1,
            transport="quic",
            k=1,
            allow_dirty=True,
            dirty=False,
        )


@pytest.mark.asyncio
async def test_unknown_scenario_refused() -> None:
    harness = MeasurementHarness()
    with pytest.raises(HarnessError, match="unknown scenario"):
        await harness.run(
            RunConfig(
                seed=1,
                transport="bus",
                allow_dirty=True,
                scenario_name="no-such-scenario",
                executor_workers=4,
                simulated_interfaces=1,
            )
        )
