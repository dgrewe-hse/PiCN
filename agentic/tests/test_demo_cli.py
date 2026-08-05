# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Demo CLI coverage: walkthrough, sweep CLI, plot paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic.benchmark.sweep import (
    SweepSpec,
    load_jsonl,
    parse_int_list,
    parse_str_list,
    run_sweep,
    write_jsonl,
)
from demo import cardiac_walkthrough, plot_metrics, run_sweep as run_sweep_cli
from demo.plot_metrics import plot_records


def test_parse_int_list_errors() -> None:
    with pytest.raises(ValueError):
        parse_int_list("5-1")
    assert parse_int_list("") == []
    assert parse_str_list("happy, adversary") == ["happy", "adversary"]


@pytest.mark.asyncio
async def test_sweep_load_write_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "a.jsonl"
    spec = SweepSpec(
        seeds=(1,),
        k_values=(2,),
        paths=("happy",),
        transport="bus",
        allow_dirty=True,
        executor_workers=8,
        simulated_interfaces=0,
    )
    await run_sweep(spec, out_path=out)
    loaded = load_jsonl(out)
    assert len(loaded) == 1
    copy = tmp_path / "b.jsonl"
    write_jsonl(copy, loaded)
    assert load_jsonl(copy)[0]["config"]["seed"] == 1


def test_run_sweep_cli(tmp_path: Path) -> None:
    out = tmp_path / "cli.jsonl"
    code = run_sweep_cli.main(
        [
            "--seeds",
            "1",
            "--k",
            "2",
            "--path",
            "happy",
            "--out",
            str(out),
            "--allow-dirty",
        ]
    )
    assert code == 0
    assert out.exists()


def test_run_sweep_cli_rejects_empty_seeds() -> None:
    with pytest.raises(SystemExit):
        run_sweep_cli.main(["--seeds", "", "--k", "2", "--allow-dirty"])


@pytest.mark.asyncio
async def test_cardiac_walkthrough_ok() -> None:
    code = await cardiac_walkthrough.run_walkthrough(
        k=2, seed=3, patient_id="cov"
    )
    assert code == 0


def test_cardiac_walkthrough_main() -> None:
    assert cardiac_walkthrough.main(["--k", "2", "--seed", "1"]) == 0


def test_cardiac_walkthrough_rejects_small_k() -> None:
    with pytest.raises(SystemExit):
        cardiac_walkthrough.main(["--k", "1"])


def test_plot_records_structural_and_m4(tmp_path: Path) -> None:
    records = [
        {
            "config": {"k": 2, "path": "happy"},
            "metrics": {
                "m1_latency_ms": 1.5,
                "context_pit_peak": 1.0,
                "dispatch_count": 5.0,
                "artefact_bytes_total": 100.0,
                "m3_publishable": False,
            },
        },
        {
            "config": {"k": 3, "path": "adversary"},
            "metrics": {
                "m1_latency_ms": 2.0,
                "context_pit_peak": 2.0,
                "dispatch_count": 8.0,
                "artefact_bytes_total": 200.0,
                "m4_detection_rate": 1.0,
                "m3_publishable": False,
            },
        },
    ]
    written = plot_records(records, tmp_path / "fig", include_m3=False)
    assert any(path.endswith("latency_vs_k.csv") for path in written)
    assert any(path.endswith("m4_detection_rate.txt") for path in written)


def test_plot_include_m3_success(tmp_path: Path) -> None:
    records = [
        {
            "config": {"k": 2, "path": "paired"},
            "metrics": {
                "m1_latency_ms": 1.0,
                "m3_overhead_ratio": 1.2,
                "m3_publishable": True,
                "context_pit_peak": 1.0,
                "dispatch_count": 4.0,
                "artefact_bytes_total": 50.0,
            },
        }
    ]
    written = plot_records(records, tmp_path / "fig", include_m3=True)
    assert any("m3_overhead_vs_k.csv" in path for path in written)
    assert any("m3_README.txt" in path for path in written)


def test_plot_metrics_main(tmp_path: Path) -> None:
    infile = tmp_path / "in.jsonl"
    infile.write_text(
        json.dumps(
            {
                "config": {"k": 2, "path": "happy"},
                "metrics": {
                    "m1_latency_ms": 1.0,
                    "context_pit_peak": 1.0,
                    "dispatch_count": 2.0,
                    "artefact_bytes_total": 10.0,
                    "m3_publishable": False,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    assert plot_metrics.main(["--in", str(infile), "--out", str(out)]) == 0
    assert list(out.iterdir())


def test_run_paired_bus_cli(tmp_path: Path) -> None:
    from demo import run_paired_bus

    out = tmp_path / "paired.jsonl"
    code = run_paired_bus.main(
        ["--seeds", "1", "--k", "2", "--out", str(out), "--allow-dirty"]
    )
    assert code == 0
    assert out.exists()
    lines = [json.loads(line) for line in out.read_text().splitlines() if line]
    assert lines[0]["metrics"]["m3_publishable"] is True
