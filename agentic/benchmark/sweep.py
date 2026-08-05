# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Multi-run sweep driver over :class:`~agentic.benchmark.harness.RunConfig`."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from agentic.benchmark.harness import MeasurementHarness, RunConfig


@dataclass
class SweepSpec:
    """Cartesian product of seeds, ``k`` values, and scenario paths.

    :param seeds: Run seeds.
    :param k_values: Hospital counts.
    :param paths: ``happy`` and/or ``adversary``.
    :param transport: Metadata transport label (``bus`` or ``udp``).
    :param allow_dirty: Permit dirty working trees.
    :param executor_workers: A-011 executor size.
    :param simulated_interfaces: A-011 simulated interface count.
    :param scenario_name: Registered scenario plugin.
    """

    seeds: Sequence[int]
    k_values: Sequence[int] = field(default_factory=lambda: (2, 3, 4, 5, 8))
    paths: Sequence[str] = field(default_factory=lambda: ("happy", "adversary"))
    transport: str = "bus"
    allow_dirty: bool = False
    executor_workers: int = 32
    simulated_interfaces: int = 0
    scenario_name: str = "cardiac-response"

    def configs(self) -> list[RunConfig]:
        """Expand the grid into individual :class:`RunConfig` instances."""
        configs: list[RunConfig] = []
        for seed in self.seeds:
            for k in self.k_values:
                for path in self.paths:
                    configs.append(
                        RunConfig(
                            seed=int(seed),
                            transport=self.transport,
                            k=int(k),
                            scenario_name=self.scenario_name,
                            allow_dirty=self.allow_dirty,
                            executor_workers=self.executor_workers,
                            simulated_interfaces=self.simulated_interfaces,
                            path=str(path),
                        )
                    )
        return configs


def parse_int_list(raw: str) -> list[int]:
    """Parse ``1-5`` or ``1,2,3`` into integers."""
    raw = raw.strip()
    if not raw:
        return []
    if "-" in raw and "," not in raw:
        start_s, end_s = raw.split("-", 1)
        start, end = int(start_s), int(end_s)
        if end < start:
            raise ValueError(f"invalid range: {raw!r}")
        return list(range(start, end + 1))
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def parse_str_list(raw: str) -> list[str]:
    """Parse comma-separated strings."""
    return [part.strip() for part in raw.split(",") if part.strip()]


async def run_sweep(
    spec: SweepSpec,
    *,
    out_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Run every config; optionally append JSONL records to ``out_path``.

    :return: List of harness result dicts (one per config).
    """
    results: list[dict[str, Any]] = []
    handle = None
    try:
        if out_path is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            handle = out_path.open("a", encoding="utf-8")
        for config in spec.configs():
            harness = MeasurementHarness()
            result = await harness.run(config)
            record = {
                "kind": "sweep_run",
                "config": {
                    "seed": config.seed,
                    "transport": config.transport,
                    "k": config.k,
                    "path": config.path,
                    "scenario_name": config.scenario_name,
                },
                "metadata": result["metadata"],
                "metrics": result["metrics"],
                "artefacts_summary": _artefact_summary(result["artefacts"]),
            }
            results.append(record)
            if handle is not None:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
    finally:
        if handle is not None:
            handle.close()
    return results


def _artefact_summary(artefacts: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "path",
        "trace_root_verified",
        "trace_root_verified_first",
        "trace_root_verified_second",
        "accountability_log_length",
        "context_pit_peak",
        "dispatch_count",
        "aggregation_complete",
        "artefact_bytes",
        "adversary_rank_index",
        "kpa_mismatches",
    )
    out: dict[str, Any] = {}
    for key in keys:
        if key in artefacts:
            value = artefacts[key]
            if key == "kpa_mismatches" and isinstance(value, list):
                out[key] = len(value)
            else:
                out[key] = value
    return out


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL records from ``path``."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Overwrite ``path`` with JSONL records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


__all__ = [
    "SweepSpec",
    "load_jsonl",
    "parse_int_list",
    "parse_str_list",
    "run_sweep",
    "write_jsonl",
]
