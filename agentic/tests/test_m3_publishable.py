# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""M3 publishability gate and structural metric emission."""

from __future__ import annotations

import pytest

from agentic.benchmark import MeasurementHarness, RunConfig
from agentic.benchmark.errors import HarnessError
from agentic.benchmark.events import MetricEvent
from agentic.benchmark.metrics import compute_metrics


def test_m3_not_publishable_without_plain_nfn_label() -> None:
    events = [
        MetricEvent(
            kind="nfn_baseline_latency",
            transport="bus",
            seed=1,
            value=10.0,
            labels={"mode": "synthetic"},
        ),
        MetricEvent(
            kind="agentic_latency",
            transport="bus",
            seed=1,
            value=20.0,
        ),
    ]
    snap = compute_metrics(events, transport="bus")
    assert snap.m3_publishable is False
    assert snap.m3_overhead_ratio == pytest.approx(2.0)


def test_m3_publishable_with_plain_nfn_label() -> None:
    events = [
        MetricEvent(
            kind="nfn_baseline_latency",
            transport="bus",
            seed=1,
            value=10.0,
            labels={"mode": "plain_nfn"},
        ),
        MetricEvent(
            kind="agentic_latency",
            transport="bus",
            seed=1,
            value=25.0,
        ),
    ]
    snap = compute_metrics(events, transport="bus")
    assert snap.m3_publishable is True
    assert snap.m3_overhead_ratio == pytest.approx(2.5)


def test_m3_undefined_when_baseline_zero() -> None:
    events = [
        MetricEvent(
            kind="nfn_baseline_latency",
            transport="bus",
            seed=1,
            value=0.0,
            labels={"mode": "plain_nfn"},
        ),
        MetricEvent(
            kind="agentic_latency",
            transport="bus",
            seed=1,
            value=10.0,
        ),
    ]
    with pytest.raises(HarnessError, match="M3 undefined"):
        compute_metrics(events, transport="bus")


@pytest.mark.asyncio
async def test_phase1_harness_omits_publishable_m3() -> None:
    harness = MeasurementHarness()
    out = await harness.run(
        RunConfig(
            seed=7,
            transport="bus",
            k=2,
            allow_dirty=True,
            executor_workers=8,
            simulated_interfaces=0,
            path="happy",
        )
    )
    assert out["metrics"]["m3_publishable"] is False
    assert out["metrics"]["m3_overhead_ratio"] is None
    assert out["metrics"]["context_pit_peak"] is not None
    assert out["metrics"]["dispatch_count"] is not None
    assert out["metrics"]["artefact_bytes_total"] is not None
