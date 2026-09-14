# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment D demo coverage: concurrency topology, CLI, and report generator.

Covers ``demo.concurrency_topology`` (real SimulationBus fan-out cell),
``demo.run_concurrency`` (CLI pairing), and ``demo.generate_concurrency_report``
(per-k rollup + figures). The zero-latency control cell is exercised so the
report can honestly state the measurement is noise-dominated at ``~0`` latency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic.benchmark.concurrency import CONCURRENCY_RUN_KIND
from demo.concurrency_topology import ConcurrencyRunResult, run_concurrency_cell


# --- concurrency_topology ---------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_cell_serial_positive_latency() -> None:
    result = await run_concurrency_cell(
        k=2, seed=1, leaf_latency_s=0.01, dispatch="serial", observer_on=True, log_level=255
    )
    assert isinstance(result, ConcurrencyRunResult)
    assert result.dispatch == "serial"
    assert result.k == 2
    assert result.elapsed_ms > 0
    assert result.trace_root_hex
    assert result.t_service_ms == pytest.approx(10.0)
    assert result.leaf_inflight_peak >= 1
    assert result.extras["transport"] == "bus"
    assert result.extras["measured"] is True


@pytest.mark.asyncio
async def test_concurrency_cell_concurrent_positive_latency() -> None:
    result = await run_concurrency_cell(
        k=4, seed=2, leaf_latency_s=0.02, dispatch="concurrent", observer_on=True, log_level=255
    )
    assert result.dispatch == "concurrent"
    assert result.k == 4
    assert result.elapsed_ms > 0
    assert result.leaf_inflight_peak >= 1
    assert result.t_service_ms == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_concurrency_cell_zero_latency_control() -> None:
    """Zero-latency control: no real T_service, measurement is noise-dominated."""
    result = await run_concurrency_cell(
        k=2, seed=3, leaf_latency_s=0.0, dispatch="concurrent", observer_on=True, log_level=255
    )
    assert result.leaf_latency_s == 0.0
    assert result.t_service_ms == 0.0
    assert result.extras["measured"] is False


@pytest.mark.asyncio
async def test_concurrency_cell_observer_off() -> None:
    result = await run_concurrency_cell(
        k=2, seed=4, leaf_latency_s=0.01, dispatch="concurrent", observer_on=False, log_level=255
    )
    assert result.elapsed_ms > 0
    # Without an observer the per-phase decomposition is absent; the wall-clock
    # t_intent is still measured from the caller-supplied emission timestamp.
    assert result.t_decompose_ms is None
    assert result.t_dispatch_ms is None
    assert result.t_network_ms is None
    assert result.t_aggregate_ms is None
    assert result.t_intent_ms is not None
    assert result.leaf_inflight_peak == 0
    assert result.leaf_overlap_fraction == 0.0


def test_concurrency_run_result_defaults() -> None:
    res = ConcurrencyRunResult(
        seed=1,
        k=2,
        leaf_latency_s=0.0,
        dispatch="serial",
        elapsed_ms=1.0,
        trace_root_hex="ab",
        t_decompose_ms=None,
        t_dispatch_ms=None,
        t_network_ms=None,
        t_service_ms=0.0,
        t_aggregate_ms=None,
        t_intent_ms=None,
        leaf_inflight_peak=0,
        leaf_overlap_fraction=0.0,
    )
    assert res.expected_subintent_set == ""
    assert res.extras == {}


# --- run_concurrency CLI ----------------------------------------------------


def test_run_concurrency_cli_paired(tmp_path: Path) -> None:
    from demo import run_concurrency as run_conc_cli

    out = tmp_path / "conc.jsonl"
    code = run_conc_cli.main(
        [
            "--seeds", "1",
            "--k", "2",
            "--leaf-latency-s", "0.01,0.05",
            "--out", str(out),
            "--summary-json", str(tmp_path / "summary.json"),
            "--allow-dirty",
        ]
    )
    assert code == 0
    lines = [json.loads(l) for l in out.read_text().splitlines() if l]
    assert len(lines) == 2
    for rec in lines:
        assert rec["kind"] == CONCURRENCY_RUN_KIND
        assert rec["transport"] == "bus"
        assert rec["fanout_speedup"] is not None
        assert rec["trace_root_equal"] is True
    assert (tmp_path / "summary.json").exists()


def test_run_concurrency_cli_serial_only(tmp_path: Path) -> None:
    from demo import run_concurrency as run_conc_cli

    out = tmp_path / "ser.jsonl"
    code = run_conc_cli.main(
        [
            "--seeds", "1",
            "--k", "2",
            "--leaf-latency-s", "0.01",
            "--concurrency", "serial",
            "--out", str(out),
            "--allow-dirty",
        ]
    )
    assert code == 0
    lines = [json.loads(l) for l in out.read_text().splitlines() if l]
    assert len(lines) == 1
    assert lines[0]["dispatch"] == "serial"


def test_run_concurrency_cli_concurrent_only(tmp_path: Path) -> None:
    from demo import run_concurrency as run_conc_cli

    out = tmp_path / "conc2.jsonl"
    code = run_conc_cli.main(
        [
            "--seeds", "1",
            "--k", "2",
            "--leaf-latency-s", "0.01",
            "--concurrency", "concurrent",
            "--out", str(out),
            "--allow-dirty",
        ]
    )
    assert code == 0
    lines = [json.loads(l) for l in out.read_text().splitlines() if l]
    assert len(lines) == 1
    assert lines[0]["dispatch"] == "concurrent"


def test_run_concurrency_cli_rejects_empty_seeds() -> None:
    from demo import run_concurrency as run_conc_cli

    with pytest.raises(SystemExit):
        run_conc_cli.main(["--seeds", "", "--k", "2", "--allow-dirty"])


def test_run_concurrency_cli_rejects_empty_k() -> None:
    from demo import run_concurrency as run_conc_cli

    with pytest.raises(SystemExit):
        run_conc_cli.main(["--seeds", "1", "--k", "", "--allow-dirty"])


def test_run_concurrency_cli_rejects_empty_latency() -> None:
    from demo import run_concurrency as run_conc_cli

    with pytest.raises(SystemExit):
        run_conc_cli.main(["--seeds", "1", "--k", "2", "--leaf-latency-s", "", "--allow-dirty"])


def test_run_concurrency_cli_rejects_dirty_tree() -> None:
    from demo import run_concurrency as run_conc_cli

    with pytest.raises(SystemExit):
        run_conc_cli.main(["--seeds", "1", "--k", "2", "--leaf-latency-s", "0.01"])


# --- generate_concurrency_report -------------------------------------------


def _publishable_record(k: int = 2, seed: int = 1) -> dict:
    return {
        "kind": CONCURRENCY_RUN_KIND,
        "transport": "bus",
        "seed": seed,
        "k": k,
        "leaf_latency_s": 0.05,
        "control": False,
        "expected_subintent_set_serial": "deadbeef",
        "expected_subintent_set_concurrent": "deadbeef",
        "trace_root_equal": True,
        "fanout_serial_ms": 20.0,
        "fanout_concurrent_ms": 5.0,
        "fanout_speedup": 4.0,
        "T_intent_view": "A",
        "t_intent_ms": 25.0,
        "t_decompose_ms": 2.0,
        "t_dispatch_ms": 10.0,
        "t_network_ms": 0.5,
        "t_service_ms": 4.0,
        "t_service_measured": True,
        "t_aggregate_ms": 8.0,
    }


def test_report_load_records_filters_kind(tmp_path: Path) -> None:
    from demo import generate_concurrency_report as gcr

    p = tmp_path / "in.jsonl"
    p.write_text(
        json.dumps(_publishable_record()) + "\n"
        + json.dumps({"kind": "other", "x": 1}) + "\n"
        + "\n",
        encoding="utf-8",
    )
    records = gcr._load_records(p)
    assert len(records) == 1
    assert records[0]["kind"] == CONCURRENCY_RUN_KIND


def test_report_referenced_kinds_finds_literal() -> None:
    from demo import generate_concurrency_report as gcr

    source = 'x = "concurrency_run"\n'
    assert gcr._referenced_kinds(source) == {CONCURRENCY_RUN_KIND}
    assert gcr._referenced_kinds("y = 'other'") == set()


def test_report_summarize_groups_by_k(tmp_path: Path) -> None:
    from demo import generate_concurrency_report as gcr

    records = [_publishable_record(k=2, seed=1), _publishable_record(k=2, seed=2),
               _publishable_record(k=3, seed=3)]
    summary = gcr._summarize(records)
    assert summary["runs"] == 3
    assert summary["publishable_runs"] == 3
    assert set(summary["speedup_by_k"]) == {"2", "3"}
    assert summary["speedup_by_k"]["2"]["mean"] == pytest.approx(4.0)
    assert "caption" in summary


def test_report_summarize_skips_non_publishable(tmp_path: Path) -> None:
    from demo import generate_concurrency_report as gcr

    records = [_publishable_record(k=2, seed=1)]
    records[0]["transport"] = "udp"
    summary = gcr._summarize(records)
    assert summary["publishable_runs"] == 0
    assert summary["speedup_by_k"] == {}


def test_report_main_writes_markdown(tmp_path: Path) -> None:
    from demo import generate_concurrency_report as gcr

    p = tmp_path / "in.jsonl"
    p.write_text(json.dumps(_publishable_record()) + "\n", encoding="utf-8")
    report_dir = tmp_path / "report"
    code = gcr.main(["--jsonl", str(p), "--report-dir", str(report_dir)])
    assert code == 0
    assert (report_dir / "concurrency_evaluation.md").exists()


def test_report_main_no_jsonl_is_noop() -> None:
    from demo import generate_concurrency_report as gcr

    assert gcr.main([]) == 0


def test_report_figures_writes_png_when_available(tmp_path: Path) -> None:
    from demo import generate_concurrency_report as gcr

    records = [_publishable_record(k=2, seed=1)]
    figs = gcr._write_figures(records, tmp_path / "figures")
    if figs:
        assert any(f.suffix == ".png" for f in figs)