# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Generate figures from demo JSONL (M3 gated on publishable baselines)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _by_k(
    records: list[dict[str, Any]], metric_key: str
) -> dict[int, list[float]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for record in records:
        metrics = record.get("metrics") or {}
        config = record.get("config") or {}
        value = metrics.get(metric_key)
        k = config.get("k")
        if value is None or k is None:
            continue
        grouped[int(k)].append(float(value))
    return dict(sorted(grouped.items()))


def _series_means(grouped: dict[int, list[float]]) -> tuple[list[int], list[float]]:
    ks = list(grouped.keys())
    vals = [mean(grouped[k]) for k in ks]
    return ks, vals


def _try_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]

        return plt
    except ImportError:
        return None


def _write_csv(path: Path, ks: list[int], values: list[float], ylabel: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"k,{ylabel}\n")
        for k, value in zip(ks, values, strict=True):
            handle.write(f"{k},{value}\n")


def _plot_line(
    plt: Any,
    out: Path,
    ks: list[int],
    values: list[float],
    *,
    title: str,
    ylabel: str,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots()
    ax.plot(ks, values, marker="o")
    ax.set_xlabel("k (hospitals)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def plot_records(
    records: list[dict[str, Any]],
    out_dir: Path,
    *,
    include_m3: bool,
) -> list[str]:
    """Write CSV (always) and PNG (if matplotlib available). Return paths written."""
    written: list[str] = []
    plt = _try_matplotlib()
    out_dir.mkdir(parents=True, exist_ok=True)

    plots = [
        ("m1_latency_ms", "latency_vs_k", "Mean latency (ms)", "Latency vs k"),
        ("context_pit_peak", "pit_peak_vs_k", "Context PIT peak", "Context PIT peak vs k"),
        ("dispatch_count", "dispatch_vs_k", "Dispatch count", "Dispatch count vs k"),
        (
            "artefact_bytes_total",
            "artefact_bytes_vs_k",
            "Artefact bytes",
            "Artefact bytes vs k",
        ),
    ]
    for metric_key, stem, ylabel, title in plots:
        grouped = _by_k(records, metric_key)
        if not grouped:
            continue
        ks, vals = _series_means(grouped)
        csv_path = out_dir / f"{stem}.csv"
        _write_csv(csv_path, ks, vals, ylabel)
        written.append(str(csv_path))
        if plt is not None:
            png_path = out_dir / f"{stem}.png"
            _plot_line(plt, png_path, ks, vals, title=title, ylabel=ylabel)
            written.append(str(png_path))

    # M4: detection rate on adversary records
    adv = [r for r in records if (r.get("config") or {}).get("path") == "adversary"]
    if adv:
        rates = [
            float(r["metrics"]["m4_detection_rate"])
            for r in adv
            if r.get("metrics", {}).get("m4_detection_rate") is not None
        ]
        if rates:
            summary = out_dir / "m4_detection_rate.txt"
            summary.write_text(
                f"mean_m4_detection_rate={mean(rates)}\nn={len(rates)}\n",
                encoding="utf-8",
            )
            written.append(str(summary))

    if include_m3:
        paired = [
            r
            for r in records
            if r.get("metrics", {}).get("m3_publishable")
            and r.get("metrics", {}).get("m3_overhead_ratio") is not None
        ]
        if not paired:
            raise SystemExit(
                "refusing --include-m3: no records with m3_publishable=true "
                "(run demo.run_paired_bus first)"
            )
        by_k: dict[int, list[float]] = defaultdict(list)
        for record in paired:
            k = int(record["config"]["k"])
            by_k[k].append(float(record["metrics"]["m3_overhead_ratio"]))
        ks, vals = _series_means(dict(sorted(by_k.items())))
        csv_path = out_dir / "m3_overhead_vs_k.csv"
        _write_csv(csv_path, ks, vals, "m3_overhead_ratio")
        written.append(str(csv_path))
        if plt is not None:
            png_path = out_dir / "m3_overhead_vs_k.png"
            _plot_line(
                plt,
                png_path,
                ks,
                vals,
                title="M3 overhead (Agentic / plain NFN)",
                ylabel="ratio",
            )
            written.append(str(png_path))
        note = out_dir / "m3_README.txt"
        note.write_text(
            "M3 ratios are publishable only for SimulationBus paired runs "
            "with labels.mode=plain_nfn baselines.\n"
            f"records={len(paired)} mean_ratio={mean(vals)}\n",
            encoding="utf-8",
        )
        written.append(str(note))

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plot / export metrics from demo JSONL (M3 gated)."
    )
    parser.add_argument("--in", dest="infile", required=True, help="Input JSONL")
    parser.add_argument(
        "--out",
        dest="outdir",
        default="demo/results/figures",
        help="Output directory for CSV/PNG",
    )
    parser.add_argument(
        "--include-m3",
        action="store_true",
        help="Emit M3 figures (requires m3_publishable records)",
    )
    args = parser.parse_args(argv)
    records = _load(Path(args.infile))
    if not records:
        print("No records in input.", file=sys.stderr)
        return 1
    written = plot_records(records, Path(args.outdir), include_m3=args.include_m3)
    print(f"Wrote {len(written)} artefacts under {args.outdir}")
    for path in written:
        print(f"  {path}")
    if _try_matplotlib() is None:
        print("matplotlib not installed — CSV only (pip install matplotlib).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
