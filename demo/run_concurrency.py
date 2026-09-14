# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""CLI: Experiment D — concurrent fan-out + T_intent decomposition (design v4 §4.4).

Runs **paired** serial/concurrent cells over a ``SimulationBus`` with registered
``LatencyBackend`` producers. One JSONL record per ``(seed, k, leaf_latency_s)``
carries both modes' T_intent decomposition, the speedup, peak in-flight,
overlap, ``trace_root_equal``, and run metadata.

Zero-latency control cells (``leaf_latency_s=0``, expected ~1.0x) are included
in every campaign and are structurally bound to ``control=true`` by the gate.

Honest framing: SimulationBus mechanism illustration on a single host;
deterministic backends; ``T_network`` near-zero by construction; not a
deployment measurement (design v4 §5.5).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agentic.benchmark.concurrency import CONCURRENCY_RUN_KIND, fanout_speedup
from agentic.benchmark.metadata import working_tree_dirty
from agentic.benchmark.sweep import parse_int_list, parse_str_list
from demo.concurrency_topology import ConcurrencyRunResult, run_concurrency_cell

_DEFAULT_OUT = Path(__file__).resolve().parent / "results" / "concurrency.jsonl"
_DEFAULT_SUMMARY = Path(__file__).resolve().parent / "results" / "concurrency_summary.json"


def _t_intent_dict(run: ConcurrencyRunResult) -> dict[str, Any]:
    return {
        "elapsed_ms": run.elapsed_ms,
        "trace_root_hex": run.trace_root_hex,
        "t_decompose_ms": run.t_decompose_ms,
        "t_dispatch_ms": run.t_dispatch_ms,
        "t_network_ms": run.t_network_ms,
        "t_service_ms": run.t_service_ms,
        "t_aggregate_ms": run.t_aggregate_ms,
        "t_intent_ms": run.t_intent_ms,
        "leaf_inflight_peak": run.leaf_inflight_peak,
        "leaf_overlap_fraction": run.leaf_overlap_fraction,
    }


def _cell_record(
    *, serial: ConcurrencyRunResult, concurrent: ConcurrencyRunResult
) -> dict[str, Any]:
    speedup = fanout_speedup(
        serial_ms=serial.elapsed_ms, concurrent_ms=concurrent.elapsed_ms
    )
    return {
        "kind": CONCURRENCY_RUN_KIND,
        "transport": "bus",
        "seed": serial.seed,
        "k": serial.k,
        "leaf_latency_s": serial.leaf_latency_s,
        "control": serial.leaf_latency_s == 0.0,
        "expected_subintent_set_serial": serial.expected_subintent_set,
        "expected_subintent_set_concurrent": concurrent.expected_subintent_set,
        "trace_root_equal": serial.trace_root_hex == concurrent.trace_root_hex,
        "fanout_serial_ms": serial.elapsed_ms,
        "fanout_concurrent_ms": concurrent.elapsed_ms,
        "fanout_speedup": speedup,
        "leaf_inflight_peak": concurrent.leaf_inflight_peak,
        "leaf_overlap_fraction": concurrent.leaf_overlap_fraction,
        "T_intent_view": "A",
        "t_intent_ms": concurrent.t_intent_ms,
        "t_decompose_ms": concurrent.t_decompose_ms,
        "t_dispatch_ms": concurrent.t_dispatch_ms,
        "t_network_ms": concurrent.t_network_ms,
        "t_service_ms": concurrent.t_service_ms,
        "t_service_measured": (
            concurrent.leaf_latency_s > 0
            and concurrent.extras.get("t_service_measured_ms") is not None
        ),
        "t_aggregate_ms": concurrent.t_aggregate_ms,
        "observer_overhead_ms": None,
        "serial": _t_intent_dict(serial),
        "concurrent": _t_intent_dict(concurrent),
        "metadata": {
            "seed": serial.seed,
            "k": serial.k,
            "leaf_latency_s": serial.leaf_latency_s,
            "transport": "bus",
            "backend": "deterministic",
            "observer": None,
            "nature": "simulation_bus_mechanism_illustration",
        },
    }


async def _run_paired(
    *,
    seed: int,
    k: int,
    leaf_latency_s: float,
    observer_on: bool,
    log_level: int,
) -> dict[str, Any]:
    serial = await run_concurrency_cell(
        k=k, seed=seed, leaf_latency_s=leaf_latency_s, dispatch="serial",
        observer_on=observer_on, log_level=log_level,
    )
    concurrent = await run_concurrency_cell(
        k=k, seed=seed, leaf_latency_s=leaf_latency_s, dispatch="concurrent",
        observer_on=observer_on, log_level=log_level,
    )
    return _cell_record(serial=serial, concurrent=concurrent)


async def _async_main(args: argparse.Namespace) -> int:
    seeds = parse_int_list(args.seeds)
    k_values = parse_int_list(args.k)
    leaf_latencies = [float(x) for x in parse_str_list(args.leaf_latency_s)]
    if not seeds:
        raise SystemExit("--seeds must list at least one seed")
    if not k_values:
        raise SystemExit("--k must list at least one value")
    if not leaf_latencies:
        raise SystemExit("--leaf-latency-s must list at least one value")

    # A-011 style reproducibility gate: refuse a dirty tree unless allowed.
    if working_tree_dirty(cwd=None) and not args.allow_dirty:
        raise SystemExit(
            "refusing to run from a dirty working tree without --allow-dirty"
        )

    modes = parse_str_list(args.concurrency)
    if not modes:
        raise SystemExit("--concurrency must list serial and/or concurrent")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with out.open("w", encoding="utf-8") as handle:
        for seed in seeds:
            for k in k_values:
                for leaf_latency_s in leaf_latencies:
                    if "serial" in modes and "concurrent" in modes:
                        # Paired: one record per (seed, k, leaf_latency_s).
                        record = await _run_paired(
                            seed=seed, k=k, leaf_latency_s=leaf_latency_s,
                            observer_on=args.observer == "on", log_level=args.log_level,
                        )
                        records.append(record)
                        handle.write(json.dumps(record, sort_keys=True) + "\n")
                        handle.flush()
                        print(
                            f"  seed={seed} k={k} leaf_latency_s={leaf_latency_s} "
                            f"speedup={record['fanout_speedup']}"
                        )
                    elif "serial" in modes:
                        run = await run_concurrency_cell(
                            k=k, seed=seed, leaf_latency_s=leaf_latency_s,
                            dispatch="serial", observer_on=args.observer == "on",
                            log_level=args.log_level,
                        )
                        record = {
                            "kind": CONCURRENCY_RUN_KIND, "transport": "bus",
                            "seed": seed, "k": k, "leaf_latency_s": leaf_latency_s,
                            "dispatch": "serial", "elapsed_ms": run.elapsed_ms,
                            "trace_root_hex": run.trace_root_hex,
                            "control": leaf_latency_s == 0.0,
                        }
                        records.append(record)
                        handle.write(json.dumps(record, sort_keys=True) + "\n")
                        handle.flush()
                    else:  # concurrent only
                        run = await run_concurrency_cell(
                            k=k, seed=seed, leaf_latency_s=leaf_latency_s,
                            dispatch="concurrent", observer_on=args.observer == "on",
                            log_level=args.log_level,
                        )
                        record = {
                            "kind": CONCURRENCY_RUN_KIND, "transport": "bus",
                            "seed": seed, "k": k, "leaf_latency_s": leaf_latency_s,
                            "dispatch": "concurrent", "elapsed_ms": run.elapsed_ms,
                            "trace_root_hex": run.trace_root_hex,
                            "control": leaf_latency_s == 0.0,
                        }
                        records.append(record)
                        handle.write(json.dumps(record, sort_keys=True) + "\n")
                        handle.flush()

    print(f"Wrote {len(records)} records → {out}")
    if args.summary_json:
        summary = _summarize(records)
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"Wrote summary → {summary_path}")
    return 0


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"runs": len(records), "kind": CONCURRENCY_RUN_KIND}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Experiment D: concurrent fan-out + T_intent decomposition on "
            "SimulationBus. Paired serial/concurrent cells with registered "
            "LatencyBackend producers; zero-latency control cells included."
        )
    )
    parser.add_argument(
        "--concurrency",
        default="serial,concurrent",
        help="Modes to run: 'serial', 'concurrent', or both (comma list)",
    )
    parser.add_argument(
        "--leaf-latency-s",
        default="0,0.01,0.05,0.1,0.5",
        help="Per-leaf LatencyBackend latency (seconds, comma list)",
    )
    parser.add_argument("--k", default="2,3,4,5,8", help="Leaf counts (comma/range)")
    parser.add_argument("--seeds", default="1-5", help="Seeds (range or list)")
    parser.add_argument("--observer", choices=("on", "off"), default="on")
    parser.add_argument("--transport", choices=("bus",), default="bus")
    parser.add_argument("--out", default=str(_DEFAULT_OUT), help="JSONL output path")
    parser.add_argument(
        "--summary-json", default="", help="Write a summary JSON to this path"
    )
    parser.add_argument(
        "--allow-dirty", action="store_true", help="Permit dirty working trees"
    )
    parser.add_argument("--log-level", type=int, default=255)
    args = parser.parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())