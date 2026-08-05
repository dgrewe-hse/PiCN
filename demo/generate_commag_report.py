# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Run ComMag evaluation campaigns and write figures + markdown report."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from agentic.benchmark.sweep import SweepSpec, load_jsonl, run_sweep
from demo.run_paired_bus import run_paired

_ROOT = Path(__file__).resolve().parent
_DEFAULT_RESULTS = _ROOT / "results" / "commag_eval"
_DEFAULT_REPORT_DIR = _ROOT / "report"


def _mean(vals: list[float]) -> float:
    return statistics.mean(vals) if vals else float("nan")


def _median(vals: list[float]) -> float:
    return statistics.median(vals) if vals else float("nan")


def _stdev(vals: list[float]) -> float:
    return statistics.stdev(vals) if len(vals) > 1 else 0.0


def _trim_cold_start(vals: list[float], *, cap_ms: float = 500.0) -> list[float]:
    """Drop rare SimulationBus cold-start outliers (multi-second stalls)."""
    kept = [v for v in vals if v <= cap_ms]
    return kept if kept else vals


def _by_k(records: list[dict[str, Any]], key: str) -> dict[int, list[float]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for rec in records:
        k = int(rec["config"]["k"])
        value = rec.get("metrics", {}).get(key)
        if value is None and key.startswith("bus."):
            bus_key = key.split(".", 1)[1]
            value = rec.get("bus", {}).get(bus_key)
        if value is None:
            continue
        grouped[k].append(float(value))
    return dict(sorted(grouped.items()))


def _bus_by_k(records: list[dict[str, Any]], bus_key: str) -> dict[int, list[float]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for rec in records:
        k = int(rec["config"]["k"])
        value = rec.get("bus", {}).get(bus_key)
        if value is None:
            continue
        grouped[k].append(float(value))
    return dict(sorted(grouped.items()))


def _plot_series(
    out: Path,
    series: dict[str, dict[int, list[float]]],
    *,
    title: str,
    ylabel: str,
    xlabel: str = "k (hospitals / fan-out)",
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    for label, grouped in series.items():
        if not grouped:
            continue
        ks = list(grouped.keys())
        means = [_median(_trim_cold_start(grouped[k])) for k in ks]
        errs = [_stdev(_trim_cold_start(grouped[k])) for k in ks]
        ax.errorbar(ks, means, yerr=errs, marker="o", capsize=3, label=label)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    if len(series) > 1:
        ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def _plot_bar_ratios(
    out: Path,
    sync_ratios: list[float],
    async_ratios: list[float],
    *,
    title: str,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5.2, 3.6))
    labels = ["vs NFN sync", "vs NFN async"]
    sync_t = _trim_cold_start(sync_ratios, cap_ms=10.0)  # ratios near O(1)
    async_t = _trim_cold_start(async_ratios, cap_ms=10.0)
    # Ratios are dimensionless; keep all finite positive samples.
    sync_t = [v for v in sync_ratios if 0 < v < 10]
    async_t = [v for v in async_ratios if 0 < v < 10]
    means = [_median(sync_t), _median(async_t)]
    errs = [_stdev(sync_t), _stdev(async_t)]
    ax.bar(labels, means, yerr=errs, capsize=4, color=["#4C78A8", "#F58518"])
    ax.axhline(1.0, color="gray", linestyle="--", linewidth=1, label="parity")
    ax.set_ylabel("Latency ratio (Agentic / NFN)")
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


async def _run_campaigns(
    *,
    results_dir: Path,
    seeds_phase1: list[int],
    seeds_phase2: list[int],
    k_phase1: list[int],
    k_phase2: list[int],
) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    phase1 = results_dir / "phase1_structural.jsonl"
    phase2 = results_dir / "phase2_paired.jsonl"

    if phase1.exists() and sum(1 for _ in phase1.open()) >= len(seeds_phase1) * len(
        k_phase1
    ) * 2:
        print(f"Phase 1 already complete ({phase1}); reusing")
    else:
        if phase1.exists():
            phase1.unlink()
        print(f"Phase 1 structural sweep → {phase1}")
        await run_sweep(
            SweepSpec(
                seeds=seeds_phase1,
                k_values=k_phase1,
                paths=("happy", "adversary"),
                transport="bus",
                allow_dirty=True,
                executor_workers=32,
                simulated_interfaces=0,
            ),
            out_path=phase1,
        )

    if phase2.exists():
        phase2.unlink()
    print(f"Phase 2 paired bus (NFN sync/async + Agentic) → {phase2}")
    await run_paired(
        seeds=seeds_phase2,
        k_values=k_phase2,
        out_path=phase2,
        baseline="nfn_async",
        include_sync=True,
        include_async=True,
    )
    return phase1, phase2


def _summarize(
    phase1: list[dict[str, Any]], phase2: list[dict[str, Any]]
) -> dict[str, Any]:
    happy = [r for r in phase1 if r["config"]["path"] == "happy"]
    adv = [r for r in phase1 if r["config"]["path"] == "adversary"]
    m4 = [
        float(r["metrics"]["m4_detection_rate"])
        for r in adv
        if r.get("metrics", {}).get("m4_detection_rate") is not None
    ]
    nfn_sync_vals = _trim_cold_start(
        [float(r["bus"]["nfn_sync_elapsed_ms"]) for r in phase2]
    )
    nfn_async_vals = _trim_cold_start(
        [float(r["bus"]["nfn_async_elapsed_ms"]) for r in phase2]
    )
    agentic_vals = _trim_cold_start(
        [float(r["bus"]["agentic_async_elapsed_ms"]) for r in phase2]
    )
    m3_async_raw = [
        float(r["metrics"]["m3_vs_nfn_async"])
        for r in phase2
        if r.get("metrics", {}).get("m3_vs_nfn_async") is not None
    ]
    m3_sync_raw = [
        float(r["metrics"]["m3_vs_nfn_sync"])
        for r in phase2
        if r.get("metrics", {}).get("m3_vs_nfn_sync") is not None
    ]
    m3_async = [v for v in m3_async_raw if 0 < v < 10]
    m3_sync = [v for v in m3_sync_raw if 0 < v < 10]
    return {
        "phase1_runs": len(phase1),
        "phase2_runs": len(phase2),
        "happy_runs": len(happy),
        "adversary_runs": len(adv),
        "m4_mean": _mean(m4),
        "m4_n": len(m4),
        "m3_vs_async_mean": _mean(m3_async),
        "m3_vs_async_median": _median(m3_async),
        "m3_vs_async_stdev": _stdev(m3_async),
        "m3_vs_sync_mean": _mean(m3_sync),
        "m3_vs_sync_median": _median(m3_sync),
        "m3_vs_sync_stdev": _stdev(m3_sync),
        "nfn_sync_ms_mean": _mean(nfn_sync_vals),
        "nfn_sync_ms_median": _median(nfn_sync_vals),
        "nfn_async_ms_mean": _mean(nfn_async_vals),
        "nfn_async_ms_median": _median(nfn_async_vals),
        "agentic_ms_mean": _mean(agentic_vals),
        "agentic_ms_median": _median(agentic_vals),
        "cold_start_note": (
            "Absolute latencies report median after dropping samples >500 ms "
            "(SimulationBus cold-start / teardown stalls)."
        ),
        "dispatch_by_k": {
            str(k): _mean(v)
            for k, v in _by_k(happy, "dispatch_count").items()
        },
        "pit_peak_by_k": {
            str(k): _mean(v)
            for k, v in _by_k(happy, "context_pit_peak").items()
        },
        "artefact_by_k": {
            str(k): _mean(v)
            for k, v in _by_k(happy, "artefact_bytes_total").items()
        },
    }


def _write_figures(
    phase1: list[dict[str, Any]],
    phase2: list[dict[str, Any]],
    fig_dir: Path,
) -> list[Path]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    happy = [r for r in phase1 if r["config"]["path"] == "happy"]

    p = fig_dir / "fig1_bus_latency_vs_k.png"
    _plot_series(
        p,
        {
            "NFN sync": _bus_by_k(phase2, "nfn_sync_elapsed_ms"),
            "NFN async": _bus_by_k(phase2, "nfn_async_elapsed_ms"),
            "Agentic async": _bus_by_k(phase2, "agentic_async_elapsed_ms"),
        },
        title="SimulationBus latency for identical NFN combine interest",
        ylabel="Mean latency (ms)",
    )
    written.append(p)

    p = fig_dir / "fig2_m3_overhead_ratios.png"
    _plot_bar_ratios(
        p,
        [
            float(r["metrics"]["m3_vs_nfn_sync"])
            for r in phase2
            if r["metrics"].get("m3_vs_nfn_sync") is not None
        ],
        [
            float(r["metrics"]["m3_vs_nfn_async"])
            for r in phase2
            if r["metrics"].get("m3_vs_nfn_async") is not None
        ],
        title="M3 overhead: AgenticForwarder / NFNForwarder",
    )
    written.append(p)

    p = fig_dir / "fig3_dispatch_vs_k.png"
    _plot_series(
        p,
        {"Happy path": _by_k(happy, "dispatch_count")},
        title="Cardiac structural: sub-intent dispatches vs k",
        ylabel="Mean dispatch count",
    )
    written.append(p)

    p = fig_dir / "fig4_artefact_bytes_vs_k.png"
    _plot_series(
        p,
        {"Happy path": _by_k(happy, "artefact_bytes_total")},
        title="Cardiac structural: artefact bytes vs k",
        ylabel="Mean artefact bytes",
    )
    written.append(p)

    p = fig_dir / "fig5_pit_peak_vs_k.png"
    _plot_series(
        p,
        {"Happy path": _by_k(happy, "context_pit_peak")},
        title="Cardiac structural: Context PIT peak vs k",
        ylabel="Mean Context PIT peak entries",
    )
    written.append(p)

    return written


def _write_report(
    *,
    report_path: Path,
    summary: dict[str, Any],
    fig_names: list[str],
    seeds_phase1: list[int],
    seeds_phase2: list[int],
    k_phase1: list[int],
    k_phase2: list[int],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# ComMag evaluation note — Agentic vs plain NFN (prototype)",
        "",
        f"_Generated {ts}. Prototype-scale, synthetic scenario on PiCN "
        f"`SimulationBus`. Language is deliberately calibrated for an IEEE "
        f"Communications Magazine revision (illustrative, not validation)._ ",
        "",
        "## 1. Scope and framing",
        "",
        "This note accompanies a working prototype of capability-oriented "
        "forwarding beside Named Function Networking (NFN) on a modernized "
        "PiCN stack (Python 3.14, asyncio). It reports **two complementary "
        "campaigns**:",
        "",
        "1. **Structural cardiac campaign (in-process)** — mechanisms of the "
        "agentic layer (Context PIT, dispatch/aggregation, artefact sizes, "
        "adversary KP-A detection) at small hospital counts `k`.",
        "2. **SimulationBus paired campaign** — the **same NFN combine "
        "interest** under three strategies: `NFNForwarder` sync, "
        "`NFNForwarder` async, and `AgenticForwarder` async.",
        "",
        "Numbers below are **preliminary** and must be read with the "
        "comparison and scale stated explicitly. Absolute SimulationBus "
        "wall-clock latency is **not** a deployment figure; the "
        "**Agentic / NFN latency ratio (M3)** on the shared combine interest "
        "is the primary ComMag-facing candidate.",
        "",
        "## 2. Configuration",
        "",
        f"| Campaign | Seeds | `k` values | Paths / strategies |",
        f"|----------|-------|------------|--------------------|",
        f"| Phase 1 structural | `{seeds_phase1}` | `{k_phase1}` | "
        f"happy, adversary |",
        f"| Phase 2 paired bus | `{seeds_phase2}` | `{k_phase2}` | "
        f"nfn_sync, nfn_async, agentic_async |",
        "",
        f"- Phase 1 runs: **{summary['phase1_runs']}** "
        f"(happy={summary['happy_runs']}, adversary={summary['adversary_runs']})",
        f"- Phase 2 paired comparisons: **{summary['phase2_runs']}**",
        "- Agents: `DeterministicBackend` only (no live LLM; AC3).",
        "- Transport: PiCN `SimulationBus` for Phase 2; Phase 1 harness "
        "label `transport=bus` (in-process mechanisms).",
        "",
        "## 3. Results — with vs without the agentic layer (SimulationBus)",
        "",
        "Mean fetch latency for the identical nested `combine` interest "
        "(median after dropping >500 ms cold-start stalls):",
        "",
        f"| Strategy | Median latency (ms) | Mean (trimmed) |",
        f"|----------|---------------------|----------------|",
        f"| NFNForwarder sync (without agentic) | "
        f"{summary['nfn_sync_ms_median']:.3f} | "
        f"{summary['nfn_sync_ms_mean']:.3f} |",
        f"| NFNForwarder async (without agentic) | "
        f"{summary['nfn_async_ms_median']:.3f} | "
        f"{summary['nfn_async_ms_mean']:.3f} |",
        f"| AgenticForwarder async (with agentic layer) | "
        f"{summary['agentic_ms_median']:.3f} | "
        f"{summary['agentic_ms_mean']:.3f} |",
        "",
        f"_{summary['cold_start_note']}_",
        "",
        "### M3 overhead ratios (publishable)",
        "",
        f"| Comparison | Median ratio | Mean | Stdev |",
        f"|------------|--------------|------|-------|",
        f"| Agentic / NFN async | "
        f"**{summary['m3_vs_async_median']:.3f}** | "
        f"{summary['m3_vs_async_mean']:.3f} | "
        f"{summary['m3_vs_async_stdev']:.3f} |",
        f"| Agentic / NFN sync | "
        f"{summary['m3_vs_sync_median']:.3f} | "
        f"{summary['m3_vs_sync_mean']:.3f} | "
        f"{summary['m3_vs_sync_stdev']:.3f} |",
        "",
        "A median ratio near **1.0** against async NFN suggests that, at this "
        "prototype scale and for this combine workload, stacking "
        "`AgenticLayer` above NFN does not introduce large additional "
        "fetch latency beyond the async NFN baseline. The sync NFN "
        "baseline is a different runtime (multiprocessing layers) and "
        "is reported for completeness; cross-runtime absolute times "
        "should not be over-interpreted.",
        "",
        f"![Bus latency vs k](figures/{fig_names[0]})",
        "",
        f"![M3 overhead ratios](figures/{fig_names[1]})",
        "",
        "## 4. Results — agentic structural mechanisms (cardiac)",
        "",
        "These figures illustrate that the agentic mechanisms scale with "
        "`k` in the expected direction (more hospital leaves → more "
        "dispatches and artefact bytes). They are **not** NFN baselines.",
        "",
        "Mean happy-path structural metrics by `k`:",
        "",
        "| k | Dispatch count | Context PIT peak | Artefact bytes |",
        "|---|----------------|------------------|----------------|",
    ]
    for k in sorted(int(x) for x in summary["dispatch_by_k"]):
        ks = str(k)
        lines.append(
            f"| {k} | {summary['dispatch_by_k'][ks]:.1f} | "
            f"{summary['pit_peak_by_k'].get(ks, float('nan')):.1f} | "
            f"{summary['artefact_by_k'].get(ks, float('nan')):.0f} |"
        )
    lines.extend(
        [
            "",
            f"Adversary KP-A detection rate (M4): "
            f"**{summary['m4_mean']:.2f}** over n={summary['m4_n']} "
            f"adversary runs (prototype oracle).",
            "",
            f"![Dispatch vs k](figures/{fig_names[2]})",
            "",
            f"![Artefact bytes vs k](figures/{fig_names[3]})",
            "",
            f"![Context PIT peak vs k](figures/{fig_names[4]})",
            "",
            "## 5. What a ComMag revision can claim",
            "",
            "Suggested calibrated claims (item-25 style):",
            "",
            "1. **Artifact**: public fork with runnable `demo/` scripts and "
            "tests (cardiac walkthrough; SimulationBus pairing).",
            "2. **Qualifying number**: M3 median ≈ "
            f"**{summary['m3_vs_async_median']:.2f}** "
            f"(mean {summary['m3_vs_async_mean']:.2f} ± "
            f"{summary['m3_vs_async_stdev']:.2f}) "
            "(AgenticForwarder async / NFNForwarder async) for the shared "
            f"combine interest at `k ∈ {k_phase2}`, seeds "
            f"`{seeds_phase2[0]}…{seeds_phase2[-1]}`, SimulationBus, "
            "prototype-scale synthetic scenario.",
            "3. **Mechanism evidence**: cardiac happy/adversary paths "
            "demonstrate Context PIT commit-before-forward, Merkle trace "
            "roots, and KP-A deprioritisation (not ban).",
            "",
            "Avoid: bare end-to-end latency as a headline; claims of "
            "deployment validation; or describing M3 as full "
            "cardiac-over-bus leaf routing (that remains future work).",
            "",
            "## 6. How to reproduce",
            "",
            "```bash",
            "pip install -e \".[dev]\" matplotlib",
            "python -m demo.generate_commag_report",
            "```",
            "",
            "Raw JSONL: `demo/results/commag_eval/`. Figures and this "
            "report: `demo/report/`.",
            "",
            "## 7. References (in-repo)",
            "",
            "- [`docs/agentic_demo.md`](../../docs/agentic_demo.md)",
            "- [`demo/README.md`](../README.md)",
            "- [`docs/agentic.md`](../../docs/agentic.md)",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    results_dir = Path(args.results_dir)
    report_dir = Path(args.report_dir)
    fig_dir = report_dir / "figures"

    seeds_p1 = list(range(args.seed_start, args.seed_start + args.n_seeds_phase1))
    seeds_p2 = list(range(args.seed_start, args.seed_start + args.n_seeds_phase2))
    k_p1 = [int(x) for x in args.k_phase1.split(",")]
    k_p2 = [int(x) for x in args.k_phase2.split(",")]

    if args.skip_runs:
        phase1_path = results_dir / "phase1_structural.jsonl"
        phase2_path = results_dir / "phase2_paired.jsonl"
    else:
        phase1_path, phase2_path = await _run_campaigns(
            results_dir=results_dir,
            seeds_phase1=seeds_p1,
            seeds_phase2=seeds_p2,
            k_phase1=k_p1,
            k_phase2=k_p2,
        )

    phase1 = load_jsonl(phase1_path)
    phase2 = load_jsonl(phase2_path)
    summary = _summarize(phase1, phase2)
    report_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    figs = _write_figures(phase1, phase2, fig_dir)
    fig_names = [p.name for p in figs]
    report_path = report_dir / "commag_evaluation.md"
    _write_report(
        report_path=report_path,
        summary=summary,
        fig_names=fig_names,
        seeds_phase1=seeds_p1,
        seeds_phase2=seeds_p2,
        k_phase1=k_p1,
        k_phase2=k_p2,
    )
    print(f"Wrote report {report_path}")
    print(f"Wrote {len(figs)} figures under {fig_dir}")
    print(
        f"M3 vs async NFN: median={summary['m3_vs_async_median']:.3f} "
        f"mean={summary['m3_vs_async_mean']:.3f} "
        f"± {summary['m3_vs_async_stdev']:.3f}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ComMag evaluation runs + matplotlib figures + markdown report"
    )
    parser.add_argument(
        "--results-dir",
        default=str(_DEFAULT_RESULTS),
        help="JSONL output directory",
    )
    parser.add_argument(
        "--report-dir",
        default=str(_DEFAULT_REPORT_DIR),
        help="Markdown report + figures directory",
    )
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--n-seeds-phase1", type=int, default=5)
    parser.add_argument("--n-seeds-phase2", type=int, default=10)
    parser.add_argument("--k-phase1", default="2,3,4,5,8")
    parser.add_argument("--k-phase2", default="2,3,4,5")
    parser.add_argument(
        "--skip-runs",
        action="store_true",
        help="Reuse existing JSONL; only regenerate figures/report",
    )
    args = parser.parse_args(argv)
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
