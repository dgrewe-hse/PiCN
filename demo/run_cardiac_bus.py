# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""CLI: paper-aligned cardiac network demo on SimulationBus.

Single-run narrative or multi-seed × multi-k sweeps with mean / median / stdev.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from agentic.benchmark.sweep import parse_int_list
from demo.cardiac_bus_topology import CardiacBusResult, run_cardiac_bus

_DEFAULT_OUT = Path(__file__).resolve().parent / "results" / "cardiac_network.jsonl"


def _result_record(result: CardiacBusResult) -> dict[str, Any]:
    return {
        "kind": "cardiac_network_bus",
        "campaign": "cardiac_network_demo",
        "config": {
            "seed": result.seed,
            "k": result.k,
            "require_paediatric": result.require_paediatric,
            "workload": "capability_interests_on_simulation_bus",
            "wire_naming": "/cap/fwd/<capability-path>",
        },
        "bus": {
            "elapsed_ms": result.elapsed_ms,
            "wire_bytes_estimate": result.wire_bytes_estimate,
            "simulated_interfaces": result.simulated_interfaces,
            "sub_intent_names": result.sub_intent_names,
        },
        "outcome": {
            "trace_root_hex": result.trace_root_hex,
            "hospital_answers": result.hospital_answers,
            "ranked": result.ranked,
            "chosen_hospital_id": result.chosen_hospital_id,
            "dispatch_count": result.dispatch_count,
            "context_pit_peak": result.context_pit_peak,
            **result.extras,
        },
        "metrics": {
            "elapsed_ms": result.elapsed_ms,
            "dispatch_count": result.dispatch_count,
            "wire_bytes_estimate": result.wire_bytes_estimate,
            "aggregation_complete": result.extras.get("aggregation_complete", 0),
            "trace_root_verified": result.extras.get("trace_root_verified", False),
        },
    }


def _print_narrative(result: CardiacBusResult) -> None:
    print("=== Cardiac network demo (SimulationBus) ===")
    print(f"seed={result.seed}  k={result.k}  paediatric_filter={result.require_paediatric}")
    print()
    print("Ambulance decomposed intent → capability Interests:")
    for name in result.sub_intent_names:
        print(f"  Interest {name}")
    print()
    print("Hospital answers (Content on /cap/fwd/hospital/beds/h*):")
    for ans in result.hospital_answers:
        print(f"  {ans}")
    print()
    if result.chosen_hospital_id:
        print(
            f"Aggregated at ambulance → choose {result.chosen_hospital_id} "
            f"(ranked {len(result.ranked)} eligible)"
        )
    else:
        print("Aggregated at ambulance → no eligible hospital after paediatric filter")
    print(f"Context~PIT trace root: {result.trace_root_hex[:32]}…")
    print(f"elapsed_ms={result.elapsed_ms:.2f}  dispatch={result.dispatch_count}")


def _stdev(vals: list[float]) -> float:
    return statistics.stdev(vals) if len(vals) > 1 else 0.0


def summarize_cardiac_network_records(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Roll up multi-run cardiac network JSONL into mean/median/stdev tables.

    :param records: JSONL records from :func:`_result_record`.
    :return: Summary dict with overall and per-``k`` latency / dispatch stats.
    """
    elapsed = [float(r["bus"]["elapsed_ms"]) for r in records]
    dispatch = [float(r["outcome"]["dispatch_count"]) for r in records]
    verified = sum(
        1 for r in records if r.get("outcome", {}).get("trace_root_verified")
    )
    by_k_elapsed: dict[int, list[float]] = defaultdict(list)
    by_k_dispatch: dict[int, list[float]] = defaultdict(list)
    for rec in records:
        k = int(rec["config"]["k"])
        by_k_elapsed[k].append(float(rec["bus"]["elapsed_ms"]))
        by_k_dispatch[k].append(float(rec["outcome"]["dispatch_count"]))

    def _roll(vals: list[float]) -> dict[str, float]:
        return {
            "n": float(len(vals)),
            "mean": statistics.mean(vals) if vals else float("nan"),
            "median": statistics.median(vals) if vals else float("nan"),
            "stdev": _stdev(vals),
            "min": min(vals) if vals else float("nan"),
            "max": max(vals) if vals else float("nan"),
        }

    return {
        "campaign": "cardiac_network_demo",
        "runs": len(records),
        "verified_runs": verified,
        "elapsed_ms": _roll(elapsed),
        "dispatch_count": _roll(dispatch),
        "elapsed_ms_by_k": {
            str(k): _roll(vals) for k, vals in sorted(by_k_elapsed.items())
        },
        "dispatch_by_k": {
            str(k): _roll(vals) for k, vals in sorted(by_k_dispatch.items())
        },
    }


def _print_sweep_summary(summary: dict[str, Any]) -> None:
    e = summary["elapsed_ms"]
    d = summary["dispatch_count"]
    print()
    print("=== Cardiac network sweep summary ===")
    print(
        f"runs={summary['runs']}  verified={summary['verified_runs']}  "
        f"elapsed_ms mean={e['mean']:.3f} median={e['median']:.3f} "
        f"stdev={e['stdev']:.3f}  (min={e['min']:.3f} max={e['max']:.3f})"
    )
    print(
        f"dispatch mean={d['mean']:.2f} median={d['median']:.2f} "
        f"stdev={d['stdev']:.3f}"
    )
    print("per-k elapsed_ms (mean ± stdev):")
    for k, roll in summary["elapsed_ms_by_k"].items():
        print(
            f"  k={k}: n={int(roll['n'])}  "
            f"{roll['mean']:.3f} ± {roll['stdev']:.3f} ms  "
            f"(median {roll['median']:.3f})"
        )


async def run_cardiac_network_sweep(
    *,
    seeds: list[int],
    k_values: list[int],
    require_paediatric: bool = True,
    adversary_index: int | None = None,
    log_level: int = 255,
    out_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Run seed×k cardiac network exchanges; optionally append JSONL.

    :return: List of JSON-serialisable records.
    """
    records: list[dict[str, Any]] = []
    handle = None
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        handle = out_path.open("a", encoding="utf-8")
    try:
        for seed in seeds:
            for k in k_values:
                print(f"cardiac_network seed={seed} k={k}…")
                result = await run_cardiac_bus(
                    k=k,
                    seed=seed,
                    require_paediatric=require_paediatric,
                    adversary_index=adversary_index,
                    log_level=log_level,
                )
                record = _result_record(result)
                records.append(record)
                if handle is not None:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                    handle.flush()
                await asyncio.sleep(0.05)
    finally:
        if handle is not None:
            handle.close()
    return records


async def _run(args: argparse.Namespace) -> int:
    seeds = parse_int_list(args.seeds) if args.seeds else [args.seed]
    k_values = parse_int_list(args.k_list) if args.k_list else [args.k]
    if any(k < 2 for k in k_values):
        raise SystemExit("each k must be >= 2")

    out = Path(args.out) if args.out else None
    if len(seeds) == 1 and len(k_values) == 1 and not args.summary_only:
        result = await run_cardiac_bus(
            k=k_values[0],
            seed=seeds[0],
            require_paediatric=not args.no_paediatric_filter,
            adversary_index=args.adversary_index,
            log_level=args.log_level,
        )
        _print_narrative(result)
        record = _result_record(result)
        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            print(f"\nAppended record → {out}")
        return 0

    # Multi-run: truncate out file when rewriting a sweep.
    if out is not None and out.exists() and args.replace_out:
        out.unlink()
    records = await run_cardiac_network_sweep(
        seeds=seeds,
        k_values=k_values,
        require_paediatric=not args.no_paediatric_filter,
        adversary_index=args.adversary_index,
        log_level=args.log_level,
        out_path=out,
    )
    summary = summarize_cardiac_network_records(records)
    _print_sweep_summary(summary)
    if args.summary_json:
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"Wrote summary → {summary_path}")
    if out is not None:
        print(f"Wrote {len(records)} records → {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Paper-aligned cardiac network demo: ambulance submit_intent "
            "→ /cap/fwd Interests on SimulationBus → hospital Content → aggregate. "
            "Use --seeds/--k-list for multi-run mean/median/stdev."
        )
    )
    parser.add_argument("--k", type=int, default=2, help="Hospital count (≥2); single-run")
    parser.add_argument(
        "--k-list",
        default="",
        help="Comma/range list of k values for a sweep (overrides --k)",
    )
    parser.add_argument("--seed", type=int, default=1, help="Single-run seed")
    parser.add_argument(
        "--seeds",
        default="",
        help="Comma/range list of seeds for a sweep (overrides --seed)",
    )
    parser.add_argument(
        "--no-paediatric-filter",
        action="store_true",
        help="Do not require paediatric_team=true when ranking",
    )
    parser.add_argument(
        "--adversary-index",
        type=int,
        default=None,
        help="Hospital index that advertises inflated beds (optional)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help=f"Append/write JSONL records (e.g. {_DEFAULT_OUT})",
    )
    parser.add_argument(
        "--replace-out",
        action="store_true",
        help="Truncate --out before a multi-run sweep",
    )
    parser.add_argument(
        "--summary-json",
        type=str,
        default="",
        help="Write mean/median/stdev summary JSON for a multi-run sweep",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Force sweep mode even for a single seed×k (prints stdev table)",
    )
    parser.add_argument("--log-level", type=int, default=255)
    args = parser.parse_args(argv)
    if args.k < 2 and not args.k_list:
        parser.error("--k must be >= 2")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
