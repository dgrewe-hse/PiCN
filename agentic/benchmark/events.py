# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Raw metric events emitted during a harness run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EventKind = Literal[
    "latency_sample",
    "message",
    "nfn_baseline_latency",
    "agentic_latency",
    "detection",
    "scale_point",
    "context_pit_peak",
    "context_pit_entries",
    "dispatch_count",
    "aggregation_complete",
    "artefact_bytes",
]


@dataclass(frozen=True, slots=True)
class MetricEvent:
    """One raw observation. Rollups derive aggregates; never emit rollups here.

    :param kind: Event discriminator.
    :param transport: Transport that produced the event (always set).
    :param seed: Run seed.
    :param value: Primary numeric payload (latency ms, bytes, 0/1 detection, …).
    :param labels: Extra dimensions (must not invent a transport-free aggregate).
        For ``nfn_baseline_latency``, set ``labels[\"mode\"]`` to ``\"plain_nfn\"``
        only when the sample comes from a real plain-NFN paired run; otherwise
        omit the baseline or use ``mode=\"synthetic\"`` (never publishable).
    """

    kind: EventKind
    transport: str
    seed: int
    value: float
    labels: dict[str, Any] | None = None


__all__ = ["EventKind", "MetricEvent"]
