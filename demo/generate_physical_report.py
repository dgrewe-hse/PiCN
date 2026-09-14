# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment E report generator: physical testbed rollup + honest captions.

Reads **only** ``kind == "physical_run"`` records (design v4 §5.5 namespace
isolation — enforced by a *non-self-referential* AST check on this file's own
source, see :func:`_referenced_kinds` and the shared known-kind set). Groups
runs into cells, evaluates the ``physical_publishable`` gate per cell, and
emits the n/median/mean/stdev table with the mandatory physical-deployment
caption.

Mandatory caption (every E figure/table): *"Physical deployment on N
Raspberry Pi 5 nodes over UDP at commit <sha>; measured wall-clock includes
real transport and (for the LLM variant) model inference, reported
separately."* LLM cells are additionally captioned as single-edge/shared-edge
measurements unless multi-edge breadth was required (NF-5).
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

from agentic.benchmark.physical import (
    KNOWN_RUN_KINDS,
    PHYSICAL_RUN_KIND,
    build_physical_cell,
    cell_key,
    physical_publishable,
)

# Namespace contract: this generator's source must reference exactly this
# kind-literal set (checked by test_report_namespace_isolation).
_EXPECTED_KIND_LITERALS: frozenset[str] = frozenset({"physical_run"})

HONEST_CAPTION = (
    "Physical deployment on {hosts} Raspberry Pi 5 nodes over UDP at commit "
    "{commit}; measured wall-clock includes real transport and (for the LLM "
    "variant) model inference, reported separately; T_intent decomposed with "
    "producer-side service time clock-offset-corrected."
)

LLM_SINGLE_EDGE_NOTE = (
    "LLM cell: single-edge/shared-edge measurement; the multi-edge fan-out "
    "claim is not made for this cell (multi_edge_breadth_required=false, "
    "design v4 NF-5)."
)


def _referenced_kinds(source: str) -> set[str]:
    """Return every known run-kind literal referenced in ``source``.

    The scan filters against the shared full known-kind set — not against the
    expected set — so it reports what the source actually says.
    """
    tree = ast.parse(source)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value in KNOWN_RUN_KINDS
    }


def _load_records(path: Path) -> list[dict[str, Any]]:
    """Load only ``physical_run`` run records from a JSONL file."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("kind") == PHYSICAL_RUN_KIND:
                records.append(record)
    return records


def _group_cells(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group run records into cell snapshots (one per cell key).

    Records already carrying ``record_type="cell"`` are taken as-is so a
    collector-merged JSONL needs no rebuilding.
    """
    cells: list[dict[str, Any]] = []
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("record_type", "run") == "cell":
            cells.append(record)
            continue
        buckets.setdefault(tuple(sorted(cell_key(record).items())), []).append(record)
    for runs in buckets.values():
        try:
            cells.append(build_physical_cell(runs))
        except ValueError:
            continue
    return cells


def _format_cell_row(cell: dict[str, Any]) -> str:
    cell_key_str = (
        f"seed={cell['cell_key']['seed']} k={cell['cell_key']['k']} "
        f"backend={cell['backend']} placement={cell['llm_placement']} "
        f"edges={cell['edges']}"
    )
    gate = "PASS" if physical_publishable(cell) else "FAIL"
    return (
        f"| {cell_key_str} | {cell['n']} | {cell['median_ms']:.3f} | "
        f"{cell['mean_ms']:.3f} | {cell['stdev_ms']:.3f} | {gate} |"
    )


def _write_report(
    *,
    report_path: Path,
    cells: list[dict[str, Any]],
    total_records: int,
    commit: Any,
    hosts: Any,
    fig_names: list[str],
) -> None:
    caption = HONEST_CAPTION.format(hosts=hosts, commit=commit)
    lines = [
        "# Experiment E — Physical testbed over UDP",
        "",
        f"> {caption}",
        "",
        f"- Records scanned (kind={PHYSICAL_RUN_KIND}): {total_records}",
        f"- Cells: {len(cells)}",
        f"- Publishable cells (gate `physical_publishable`): "
        f"{sum(1 for c in cells if physical_publishable(c))}",
        "",
        "## Per-cell wall-clock (concurrent fan-out)",
        "",
        "| cell | n | median ms | mean ms | stdev ms | gate |",
        "|------|------|------|------|------|------|",
    ]
    cells.sort(key=lambda c: (str(c["backend"]), c["cell_key"]["seed"], c["cell_key"]["k"]))
    for cell in cells:
        lines.append(_format_cell_row(cell))
        if cell["backend"] == "llm" and cell["multi_edge_breadth_required"] is False:
            lines.append("")
            lines.append(f"*{LLM_SINGLE_EDGE_NOTE}*")
    lines.append("")
    if fig_names:
        lines.append("## Figures")
        lines.append("")
        for name in fig_names:
            lines.append(f"![{name}]({name})")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*{caption}*")
    lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _write_figures(cells: list[dict[str, Any]], fig_dir: Path) -> list[Path]:
    """Per-cell median wall-clock bars (skipped when matplotlib is absent)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    fig_dir.mkdir(parents=True, exist_ok=True)
    labels = [
        f"s{c['cell_key']['seed']}/k{c['cell_key']['k']}/{c['backend']}"
        for c in cells
    ]
    medians = [c["median_ms"] for c in cells]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, medians)
    ax.set_ylabel("median t_intent (ms)")
    ax.set_title("Physical testbed: median T_intent per cell")
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)
    caption = HONEST_CAPTION.format(
        hosts=cells[0]["metadata"].get("edges") if cells else "?",
        commit=cells[0]["picn_commit"] if cells else "<unknown>",
    )
    ax.text(0.01, 0.98, caption, transform=ax.transAxes, fontsize=5, va="top")
    fig.tight_layout()
    path = fig_dir / "physical_median_t_intent.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return [path]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Experiment E report: physical testbed cells + physical_publishable "
            "gate. Reads only kind=physical_run records."
        )
    )
    parser.add_argument("--jsonl", default="", help="Input physical JSONL")
    parser.add_argument("--report-dir", default="", help="Output report directory")
    args = parser.parse_args(argv)

    if not args.jsonl:
        return 0
    records = _load_records(Path(args.jsonl))
    cells = _group_cells(records)
    commit = records[0].get("metadata", {}).get("picn_commit") if records else "<unknown>"
    hosts = max(
        (len(r.get("metadata", {}).get("hosts", [])) for r in records), default=0
    )
    fig_dir = Path(args.report_dir) if args.report_dir else Path(".")
    figs = _write_figures(cells, fig_dir / "figures")
    report = fig_dir / "physical_evaluation.md"
    _write_report(
        report_path=report,
        cells=cells,
        total_records=len(records),
        commit=commit,
        hosts=hosts,
        fig_names=[f.name for f in figs],
    )
    print(f"Wrote report -> {report}")
    print(
        f"Publishable cells: {sum(1 for c in cells if physical_publishable(c))}"
        f"/{len(cells)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
