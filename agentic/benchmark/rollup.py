# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Transport-keyed rollups derived from raw events."""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from agentic.benchmark.errors import HarnessError
from agentic.benchmark.events import MetricEvent
from agentic.benchmark.metrics import MetricsSnapshot, compute_metrics


def rollup_by_transport(events: Sequence[MetricEvent]) -> dict[str, MetricsSnapshot]:
    """Group raw events by ``transport`` and derive per-transport metrics.

    Never aggregates across transports. Every grouping key includes transport.

    :raises HarnessError: If any event lacks a transport label.
    """
    grouped: dict[str, list[MetricEvent]] = defaultdict(list)
    for event in events:
        if not event.transport:
            raise HarnessError("rollup refuses events without transport")
        grouped[event.transport].append(event)
    return {
        transport: compute_metrics(group, transport=transport)
        for transport, group in sorted(grouped.items())
    }


__all__ = ["rollup_by_transport"]
