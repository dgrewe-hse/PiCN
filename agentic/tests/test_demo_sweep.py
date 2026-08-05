# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Sweep driver and plot M3 gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.benchmark.sweep import SweepSpec, parse_int_list, run_sweep
from demo.plot_metrics import plot_records


def test_parse_int_list_range_and_csv() -> None:
    assert parse_int_list("1-3") == [1, 2, 3]
    assert parse_int_list("2,4,8") == [2, 4, 8]


@pytest.mark.asyncio
async def test_sweep_writes_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "sweep.jsonl"
    spec = SweepSpec(
        seeds=(1,),
        k_values=(2,),
        paths=("happy",),
        transport="bus",
        allow_dirty=True,
        executor_workers=8,
        simulated_interfaces=0,
    )
    results = await run_sweep(spec, out_path=out)
    assert len(results) == 1
    assert out.exists()
    assert results[0]["metrics"]["m3_publishable"] is False


def test_plot_refuses_m3_without_publishable(tmp_path: Path) -> None:
    records = [
        {
            "config": {"k": 2, "path": "happy"},
            "metrics": {
                "m1_latency_ms": 1.0,
                "m3_overhead_ratio": 2.0,
                "m3_publishable": False,
            },
        }
    ]
    with pytest.raises(SystemExit):
        plot_records(records, tmp_path / "fig", include_m3=True)
