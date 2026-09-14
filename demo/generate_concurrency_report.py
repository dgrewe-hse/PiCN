# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment D report generator: speedup-vs-k curve + T_intent stacked bars.

Reads **only** ``kind == \"concurrency_run\"`` records (design v4 §4.5 namespace
isolation — enforced by an AST check on this file's own source). Produces a
speedup-vs-``k`` curve and a T_intent stacked-bar figure, each with honest
SimulationBus framing in its caption (design v4 §5.5).

Honest framing (every caption): *\"SimulationBus mechanism illustration on a
single host; deterministic backends; T_network is near-zero by construction;
not a deployment measurement.\"*
"""

from __future__ import annotations

import argparse
import ast
import json
import statistics
from pathlib import Path
from typing import Any

from agentic.benchmark.concurrency import CONCURRENCY_RUN_KIND, concurrent_publishable

# The exact set of kind literals this generator may reference. Enforced by an
# AST scan of this file's own source (design v4 §4.5).
_REFERENCED_KINDS: set[str] = {CONCURRENCY_RUN_KIND}

HONEST_CAPTION = (
    "SimulationBus mechanism illustration on a single host; deterministic "
    "backends; T_network is near-zero by construction; not a deployment "
    "measurement."
)


def _referenced_kinds(source: str) -> set[str]:
    """Return the ``kind`` string literals referenced in ``source``."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in _REFERENCED_KINDS:
                found.add(node.value)
    return found


def _load_records(path: Path) -> list[dict[str, Any]]:
    """Load ``concurrency_run`` records from a JSONL file."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("kind") == CONCURRENCY_RUN_KIND:
                records.append(record)
    return records


def _group_by_k(records: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Group publishable records by ``k``."""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        if not concurrent_publishable(record):
            continue
        grouped.setdefault(int(record["k"]), []).append(record)
    return grouped


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up publishable records into a per-k speedup summary."""
    grouped = _group_by_k(records)
    by_k: dict[str, dict[str, float]] = {}
    for k in sorted(grouped):
        speedups = [float(r["fanout_speedup"]) for r in grouped[k]]
        by_k[str(k)] = {
            "n": float(len(speedups)),
            "mean": statistics.mean(speedups) if speedups else float("nan"),
            "median": statistics.median(speedups) if speedups else float("nan"),
            "stdev": statistics.stdev(speedups) if len(speedups) > 1 else 0.0,
        }
    publishable = sum(1 for r in records if concurrent_publishable(r))
    return {
        "runs": len(records),
        "publishable_runs": publishable,
        "speedup_by_k": by_k,
        "caption": HONEST_CAPTION,
        "t_network_note": "T_network is near-zero by construction (SimulationBus).",
    }


def _write_figures(
    records: list[dict[str, Any]], fig_dir: Path
) -> list[Path]:
    """Write the speedup-vs-k and T_intent stacked-bar figures."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    fig_dir.mkdir(parents=True, exist_ok=True)
    figs: list[Path] = []

    # Figure 1: speedup-vs-k curve (mean ± stdev).
    grouped = _group_by_k(records)
    ks = sorted(grouped)
    if ks:
        means = [
            statistics.mean(float(r["fanout_speedup"]) for r in grouped[k]) for k in ks
        ]
        stdevs = [
            statistics.stdev([float(r["fanout_speedup"]) for r in grouped[k]])
            if len(grouped[k]) > 1
            else 0.0
            for k in ks
        ]
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.errorbar(ks, means, yerr=stdevs, marker="o", capsize=3)
        ax.set_xlabel("leaf count k")
        ax.set_ylabel("speedup (serial / concurrent)")
        ax.set_title("Concurrent fan-out speedup vs. k")
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.text(0.01, 0.98, HONEST_CAPTION, transform=ax.transAxes, fontsize=6, va="top")
        fig.tight_layout()
        path = fig_dir / "concurrency_speedup_vs_k.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        figs.append(path)

    # Figure 2: T_intent stacked bars (per k, mean of each component).
    if ks:
        comps = ["t_decompose_ms", "t_dispatch_ms", "t_network_ms", "t_service_ms", "t_aggregate_ms"]
        fig, ax = plt.subplots(figsize=(7, 4))
        bottom = [0.0] * len(ks)
        for comp in comps:
            vals = []
            for k in ks:
                comp_vals = [
                    float(r.get(comp) or 0.0)
                    for r in grouped[k]
                    if r.get(comp) is not None
                ]
                vals.append(statistics.mean(comp_vals) if comp_vals else 0.0)
            ax.bar([str(k) for k in ks], vals, bottom=bottom, label=comp)
            bottom = [b + v for b, v in zip(bottom, vals)]
        ax.set_xlabel("leaf count k")
        ax.set_ylabel("T_intent component (ms)")
        ax.set_title("T_intent decomposition (concurrent dispatch)")
        ax.legend(fontsize=6)
        ax.text(0.01, 0.98, HONEST_CAPTION, transform=ax.transAxes, fontsize=6, va="top")
        fig.tight_layout()
        path = fig_dir / "concurrency_t_intent_stack.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        figs.append(path)

    return figs


def _write_report(
    *, report_path: Path, summary: dict[str, Any], fig_names: list[str]
) -> None:
    lines = [
        "# Experiment D — Concurrent fan-out and T_intent decomposition",
        "",
        f"> {HONEST_CAPTION}",
        "",
        f"- Records: {summary['runs']}",
        f"- Publishable cells (gate `concurrent_publishable`): {summary['publishable_runs']}",
        f"- {summary['t_network_note']}",
        "",
        "## Speedup vs. leaf count k",
        "",
        "| k | n | mean speedup | median | stdev |",
        "|---|------|------|--------|-------|",
    ]
    for k, stats in summary["speedup_by_k"].items():
        lines.append(
            f"| {k} | {int(stats['n'])} | {stats['mean']:.3f} | "
            f"{stats['median']:.3f} | {stats['stdev']:.3f} |"
        )
    lines.append("")
    if fig_names:
        lines.append("## Figures")
        lines.append("")
        for name in fig_names:
            lines.append(f"![{name}]({name})")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*" + HONEST_CAPTION + "*")
    lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Experiment D report: speedup-vs-k + T_intent stacked bars. "
            "Reads only kind=concurrency_run records."
        )
    )
    parser.add_argument("--jsonl", default="", help="Input concurrency JSONL")
    parser.add_argument("--report-dir", default="", help="Output report directory")
    args = parser.parse_args(argv)

    if args.jsonl:
        records = _load_records(Path(args.jsonl))
        summary = _summarize(records)
        fig_dir = Path(args.report_dir) if args.report_dir else Path(".")
        figs = _write_figures(records, fig_dir / "figures")
        report = fig_dir / "concurrency_evaluation.md"
        _write_report(report_path=report, summary=summary, fig_names=[f.name for f in figs])
        print(f"Wrote report → {report}")
        print(f"Publishable cells: {summary['publishable_runs']}/{summary['runs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())