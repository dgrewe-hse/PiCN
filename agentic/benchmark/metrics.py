# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Derive M1–M5 and structural rollups from raw events."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Sequence

from agentic.benchmark.errors import HarnessError
from agentic.benchmark.events import MetricEvent


def _baseline_is_publishable(events: Sequence[MetricEvent]) -> bool:
    """True only when every NFN baseline event is labelled ``mode=plain_nfn``."""
    baselines = [e for e in events if e.kind == "nfn_baseline_latency"]
    if not baselines:
        return False
    for event in baselines:
        labels = event.labels or {}
        if labels.get("mode") != "plain_nfn":
            return False
    return True


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Per-transport metrics for one run.

    :param transport: Transport label (required for publication rules).
    :param m1_latency_ms: Mean end-to-end latency (absolute; publish from UDP).
    :param m2_message_count: Message count.
    :param m2_message_bytes: Total message bytes.
    :param m3_overhead_ratio: Agentic / plain-NFN latency ratio (same transport).
    :param m3_publishable: True only for real plain-NFN paired baselines.
    :param m4_detection_rate: Fraction of true positives among detection events.
    :param m5_scale_latency_by_k: Mean latency keyed by scale parameter ``k``.
    :param context_pit_peak: Peak Context PIT entry count (if observed).
    :param dispatch_count: Sub-intent dispatch count (if observed).
    :param aggregation_complete: Aggregation completions (if observed).
    :param artefact_bytes_total: Sum of artefact byte samples (if observed).
    """

    transport: str
    m1_latency_ms: float | None
    m2_message_count: int
    m2_message_bytes: float
    m3_overhead_ratio: float | None
    m3_publishable: bool
    m4_detection_rate: float | None
    m5_scale_latency_by_k: dict[int, float]
    context_pit_peak: float | None = None
    dispatch_count: float | None = None
    aggregation_complete: float | None = None
    artefact_bytes_total: float | None = None


def compute_metrics(events: Sequence[MetricEvent], *, transport: str) -> MetricsSnapshot:
    """Compute M1–M5 and structural metrics for a single transport.

    :raises HarnessError: If ``events`` mix transports.
    """
    if any(event.transport != transport for event in events):
        raise HarnessError("compute_metrics refuses mixed-transport event sets")

    latencies = [e.value for e in events if e.kind == "latency_sample"]
    messages = [e for e in events if e.kind == "message"]
    nfn = [e.value for e in events if e.kind == "nfn_baseline_latency"]
    agentic = [e.value for e in events if e.kind == "agentic_latency"]
    detections = [e.value for e in events if e.kind == "detection"]
    scale: dict[int, list[float]] = {}
    for event in events:
        if event.kind != "scale_point":
            continue
        labels = event.labels or {}
        k = int(labels.get("k", -1))
        scale.setdefault(k, []).append(event.value)

    publishable = _baseline_is_publishable(events)
    m3: float | None = None
    if publishable and nfn and agentic:
        nfn_mean = mean(nfn)
        if nfn_mean == 0:
            raise HarnessError("M3 undefined: plain NFN baseline latency is zero")
        m3 = mean(agentic) / nfn_mean
    elif nfn and agentic and not publishable:
        # Non-publishable synthetic / unpaired baselines: still compute for
        # debugging but callers must check ``m3_publishable`` before figures.
        nfn_mean = mean(nfn)
        if nfn_mean != 0:
            m3 = mean(agentic) / nfn_mean

    m4: float | None = None
    if detections:
        m4 = mean(detections)

    m5 = {k: mean(vals) for k, vals in sorted(scale.items()) if k >= 0}

    pit_peaks = [e.value for e in events if e.kind == "context_pit_peak"]
    dispatches = [e.value for e in events if e.kind == "dispatch_count"]
    aggs = [e.value for e in events if e.kind == "aggregation_complete"]
    artefact = [e.value for e in events if e.kind == "artefact_bytes"]

    return MetricsSnapshot(
        transport=transport,
        m1_latency_ms=mean(latencies) if latencies else None,
        m2_message_count=len(messages),
        m2_message_bytes=sum(e.value for e in messages),
        m3_overhead_ratio=m3,
        m3_publishable=publishable,
        m4_detection_rate=m4,
        m5_scale_latency_by_k=m5,
        context_pit_peak=max(pit_peaks) if pit_peaks else None,
        dispatch_count=sum(dispatches) if dispatches else None,
        aggregation_complete=sum(aggs) if aggs else None,
        artefact_bytes_total=sum(artefact) if artefact else None,
    )


__all__ = ["MetricsSnapshot", "compute_metrics"]
