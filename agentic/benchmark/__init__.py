# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Measurement harness: sweeps, metrics, and rollups.

Must not import ``PiCN.*``. Runs refuse to start without a seed, without
transport metadata, or from a dirty working tree (unless explicitly allowed).
Rollups are derived from raw events and always group by transport.
"""

from agentic.benchmark.capacity import assert_bus_capacity
from agentic.benchmark.errors import HarnessError
from agentic.benchmark.harness import MeasurementHarness, RunConfig
from agentic.benchmark.metadata import RunMetadata, require_run_metadata
from agentic.benchmark.metrics import MetricsSnapshot, compute_metrics
from agentic.benchmark.rollup import rollup_by_transport

__all__ = [
    "HarnessError",
    "MeasurementHarness",
    "MetricsSnapshot",
    "RunConfig",
    "RunMetadata",
    "assert_bus_capacity",
    "compute_metrics",
    "require_run_metadata",
    "rollup_by_transport",
]
