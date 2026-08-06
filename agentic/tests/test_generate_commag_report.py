# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Coverage for ComMag report rollups (mean / median / stdev) and writers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo import generate_commag_report as gcr
from demo.run_cardiac_bus import summarize_cardiac_network_records


def _structural_record(*, seed: int, k: int, path: str = "happy") -> dict:
    return {
        "kind": "harness_run",
        "config": {"seed": seed, "k": k, "path": path, "transport": "bus"},
        "metrics": {
            "dispatch_count": float(k + 4),
            "context_pit_peak": 1.0,
            "artefact_bytes_total": float(100 * k),
            "m4_detection_rate": 1.0 if path == "adversary" else None,
            "m3_publishable": False,
        },
    }


def _paired_record(*, seed: int, k: int) -> dict:
    nfn_async = 10.0 + seed
    agentic = 9.0 + seed * 0.5
    return {
        "kind": "nfn_stack_overhead_bus",
        "config": {"seed": seed, "k": k},
        "metrics": {
            "m3_vs_nfn_sync": agentic / (12.0 + seed),
            "m3_vs_nfn_async": agentic / nfn_async,
            "m3_publishable": True,
        },
        "bus": {
            "nfn_sync_elapsed_ms": 12.0 + seed,
            "nfn_async_elapsed_ms": nfn_async,
            "agentic_async_elapsed_ms": agentic,
        },
    }


def _network_record(*, seed: int, k: int) -> dict:
    return {
        "kind": "cardiac_network_bus",
        "campaign": "cardiac_network_demo",
        "config": {"seed": seed, "k": k},
        "bus": {"elapsed_ms": 5.0 + seed + k},
        "outcome": {
            "dispatch_count": k + 3,
            "trace_root_verified": True,
            "aggregation_complete": 1,
        },
    }


def test_stdev_helpers() -> None:
    assert gcr._stdev([]) == 0.0
    assert gcr._stdev([1.0]) == 0.0
    assert gcr._stdev([1.0, 3.0]) == pytest.approx(1.414, abs=0.01)
    assert gcr._mean([1.0, 3.0]) == 2.0
    assert gcr._median([1.0, 2.0, 3.0]) == 2.0
    roll = gcr._roll([2.0, 4.0, 6.0])
    assert roll["mean"] == 4.0
    assert roll["stdev"] == pytest.approx(2.0)
    assert roll["n"] == 3.0


def test_summarize_includes_stdev_and_network() -> None:
    phase1 = [
        _structural_record(seed=s, k=k, path=p)
        for s in (1, 2)
        for k in (2, 3)
        for p in ("happy", "adversary")
    ]
    phase2 = [_paired_record(seed=s, k=k) for s in (1, 2, 3) for k in (2, 3)]
    network = [_network_record(seed=s, k=k) for s in (1, 2) for k in (2, 3)]
    summary = gcr._summarize(phase1, phase2, network)
    assert summary["phase1_runs"] == 8
    assert summary["phase2_runs"] == 6
    assert summary["network_runs"] == 4
    assert summary["nfn_async_ms_stdev"] >= 0.0
    assert summary["m3_vs_async_stdev"] >= 0.0
    assert "2" in summary["dispatch_by_k_stats"]
    assert summary["dispatch_by_k_stats"]["2"]["stdev"] >= 0.0
    assert summary["cardiac_network"]["elapsed_ms"]["stdev"] >= 0.0
    assert summary["m4_stdev"] >= 0.0


def test_write_figures_and_report(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    phase1 = [
        _structural_record(seed=s, k=k, path=p)
        for s in (1, 2, 3)
        for k in (2, 3)
        for p in ("happy", "adversary")
    ]
    phase2 = [_paired_record(seed=s, k=k) for s in (1, 2, 3) for k in (2, 3)]
    network = [_network_record(seed=s, k=k) for s in (1, 2, 3) for k in (2, 3)]
    summary = gcr._summarize(phase1, phase2, network)
    fig_dir = tmp_path / "figures"
    figs = gcr._write_figures(phase1, phase2, fig_dir, network)
    assert len(figs) == 6
    assert all(p.exists() for p in figs)
    report = tmp_path / "commag_evaluation.md"
    gcr._write_report(
        report_path=report,
        summary=summary,
        fig_names=[p.name for p in figs],
        seeds_phase1=[1, 2, 3],
        seeds_phase2=[1, 2, 3],
        seeds_network=[1, 2, 3],
        k_phase1=[2, 3],
        k_phase2=[2, 3],
        k_network=[2, 3],
    )
    text = report.read_text(encoding="utf-8")
    assert "mean ± stdev" in text.lower() or "Mean ± stdev" in text
    assert "Cardiac network" in text or "cardiac network" in text
    assert "NFN stack-overhead" in text
    assert "stdev" in text.lower()


def test_summarize_cardiac_network_empty() -> None:
    assert summarize_cardiac_network_records([])["runs"] == 0


@pytest.mark.asyncio
async def test_run_campaigns_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = tmp_path / "res"
    results.mkdir()

    async def fake_sweep(spec, out_path):  # noqa: ANN001
        out_path.write_text("{}\n", encoding="utf-8")
        return []

    async def fake_paired(**kwargs):  # noqa: ANN003
        Path(kwargs["out_path"]).write_text("{}\n", encoding="utf-8")
        return []

    async def fake_network(**kwargs):  # noqa: ANN003
        Path(kwargs["out_path"]).write_text("{}\n", encoding="utf-8")
        return []

    monkeypatch.setattr(gcr, "run_sweep", fake_sweep)
    monkeypatch.setattr(gcr, "run_paired", fake_paired)
    monkeypatch.setattr(
        "demo.run_cardiac_bus.run_cardiac_network_sweep", fake_network
    )
    p1, p2, net = await gcr._run_campaigns(
        results_dir=results,
        seeds_phase1=[1],
        seeds_phase2=[1],
        seeds_network=[1],
        k_phase1=[2],
        k_phase2=[2],
        k_network=[2],
    )
    assert p1.exists() and p2.exists() and net.exists()


@pytest.mark.asyncio
async def test_run_nfn_bus_sync_path_monkeypatched(monkeypatch: pytest.MonkeyPatch) -> None:
    from demo.bus_topology import BusRunResult, run_nfn_bus

    def fake_spawn(*, k: int, seed: int, log_level: int = 255) -> BusRunResult:
        return BusRunResult(
            mode="nfn_sync",
            elapsed_ms=1.5,
            result="ok",
            k=k,
            seed=seed,
            simulated_interfaces=3,
            wire_bytes_estimate=32,
            runtime="sync",
        )

    monkeypatch.setattr("demo.bus_topology._run_nfn_bus_sync_spawn", fake_spawn)
    result = await run_nfn_bus(k=2, seed=9, runtime="sync")
    assert result.mode == "nfn_sync"
    assert result.elapsed_ms == 1.5


def test_generate_report_skip_runs(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    results = tmp_path / "results"
    report_dir = tmp_path / "report"
    results.mkdir()
    phase1 = [
        _structural_record(seed=s, k=2, path=p)
        for s in (1, 2)
        for p in ("happy", "adversary")
    ]
    phase2 = [_paired_record(seed=s, k=2) for s in (1, 2)]
    network = [_network_record(seed=s, k=2) for s in (1, 2)]
    (results / "phase1_structural.jsonl").write_text(
        "\n".join(json.dumps(r) for r in phase1) + "\n", encoding="utf-8"
    )
    (results / "phase2_paired.jsonl").write_text(
        "\n".join(json.dumps(r) for r in phase2) + "\n", encoding="utf-8"
    )
    (results / "cardiac_network.jsonl").write_text(
        "\n".join(json.dumps(r) for r in network) + "\n", encoding="utf-8"
    )
    code = gcr.main(
        [
            "--results-dir",
            str(results),
            "--report-dir",
            str(report_dir),
            "--skip-runs",
            "--n-seeds-phase1",
            "2",
            "--n-seeds-phase2",
            "2",
            "--n-seeds-network",
            "2",
            "--k-phase1",
            "2",
            "--k-phase2",
            "2",
            "--k-network",
            "2",
        ]
    )
    assert code == 0
    assert (report_dir / "commag_evaluation.md").exists()
    assert (report_dir / "summary.json").exists()
    summary = json.loads((report_dir / "summary.json").read_text())
    assert "nfn_async_ms_stdev" in summary
    assert summary["network_runs"] == 2
