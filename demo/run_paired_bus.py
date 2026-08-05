# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""SimulationBus comparison: NFN sync/async vs Agentic async (publishable M3)."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agentic.benchmark.events import MetricEvent
from agentic.benchmark.metrics import compute_metrics
from agentic.benchmark.sweep import parse_int_list
from agentic.scenario.cardiac import CardiacScenario

from demo.bus_topology import run_agentic_bus, run_nfn_bus

_DEFAULT_OUT = Path(__file__).resolve().parent / "results" / "phase2_paired.jsonl"


async def _cardiac_structural(*, k: int, seed: int) -> dict[str, Any]:
    """In-process cardiac structural artefacts for the same seed/k."""
    scenario = CardiacScenario(k=k, seed=seed)
    await scenario.setup()
    try:
        happy = await scenario.run_happy_path(patient_id=f"paired-{seed}")
        return {
            "context_pit_peak": happy["context_pit_peak"],
            "dispatch_count": happy["dispatch_count"],
            "aggregation_complete": happy["aggregation_complete"],
            "artefact_bytes": happy["artefact_bytes"],
            "trace_root_verified": happy["trace_root_verified"],
            "accountability_log_length": happy["accountability_log_length"],
        }
    finally:
        await scenario.teardown()


def _comparison_record(
    *,
    seed: int,
    k: int,
    nfn_sync_ms: float,
    nfn_async_ms: float,
    agentic_ms: float,
    wire_bytes: int,
    structural: dict[str, Any],
    nfn_sync_result: str,
    nfn_async_result: str,
    agentic_result: str,
    baseline: str,
) -> dict[str, Any]:
    """Build JSONL record with publishable M3 vs the chosen NFN baseline.

    :param baseline: ``nfn_async`` (default peer of Agentic) or ``nfn_sync``.
    """
    transport = "bus"
    if baseline == "nfn_sync":
        baseline_ms = nfn_sync_ms
        baseline_label = "nfn_sync"
    else:
        baseline_ms = nfn_async_ms
        baseline_label = "nfn_async"

    events = [
        MetricEvent(
            kind="nfn_baseline_latency",
            transport=transport,
            seed=seed,
            value=baseline_ms,
            labels={
                "mode": "plain_nfn",
                "runtime": "sync" if baseline == "nfn_sync" else "async",
                "k": k,
            },
        ),
        MetricEvent(
            kind="agentic_latency",
            transport=transport,
            seed=seed,
            value=agentic_ms,
            labels={"mode": "agentic", "runtime": "async", "k": k},
        ),
        MetricEvent(
            kind="latency_sample",
            transport=transport,
            seed=seed,
            value=agentic_ms,
            labels={"mode": "agentic", "k": k},
        ),
        MetricEvent(
            kind="message",
            transport=transport,
            seed=seed,
            value=float(wire_bytes),
            labels={"mode": "paired"},
        ),
        MetricEvent(
            kind="scale_point",
            transport=transport,
            seed=seed,
            value=agentic_ms,
            labels={"k": k},
        ),
        MetricEvent(
            kind="context_pit_peak",
            transport=transport,
            seed=seed,
            value=float(structural["context_pit_peak"]),
            labels={"k": k},
        ),
        MetricEvent(
            kind="dispatch_count",
            transport=transport,
            seed=seed,
            value=float(structural["dispatch_count"]),
            labels={"k": k},
        ),
        MetricEvent(
            kind="aggregation_complete",
            transport=transport,
            seed=seed,
            value=float(structural["aggregation_complete"]),
            labels={"k": k},
        ),
        MetricEvent(
            kind="artefact_bytes",
            transport=transport,
            seed=seed,
            value=float(structural["artefact_bytes"]),
            labels={"k": k},
        ),
    ]
    metrics = compute_metrics(events, transport=transport)
    m3_vs_sync = (
        agentic_ms / nfn_sync_ms if nfn_sync_ms > 0 else None
    )
    m3_vs_async = (
        agentic_ms / nfn_async_ms if nfn_async_ms > 0 else None
    )
    return {
        "kind": "paired_bus_run",
        "config": {
            "seed": seed,
            "transport": transport,
            "k": k,
            "path": "paired",
            "workload": "nfn_combine_same_interest",
            "strategies": ["nfn_sync", "nfn_async", "agentic_async"],
            "m3_baseline": baseline_label,
        },
        "metadata": {
            "seed": seed,
            "transport": transport,
            "k": k,
            "m3_note": (
                "Same NFN combine interest on SimulationBus under three "
                "strategies: NFNForwarder sync, NFNForwarder async, and "
                "AgenticForwarder async (companion NFN async). Publishable "
                f"M3 uses baseline={baseline_label}. Cardiac structural "
                "metrics are co-reported from the in-process scenario."
            ),
        },
        "metrics": {
            "transport": metrics.transport,
            "m1_latency_ms": metrics.m1_latency_ms,
            "m2_message_count": metrics.m2_message_count,
            "m2_message_bytes": metrics.m2_message_bytes,
            "m3_overhead_ratio": metrics.m3_overhead_ratio,
            "m3_publishable": metrics.m3_publishable,
            "m3_vs_nfn_sync": m3_vs_sync,
            "m3_vs_nfn_async": m3_vs_async,
            "m3_baseline": baseline_label,
            "m4_detection_rate": metrics.m4_detection_rate,
            "m5_scale_latency_by_k": metrics.m5_scale_latency_by_k,
            "context_pit_peak": metrics.context_pit_peak,
            "dispatch_count": metrics.dispatch_count,
            "aggregation_complete": metrics.aggregation_complete,
            "artefact_bytes_total": metrics.artefact_bytes_total,
        },
        "bus": {
            "nfn_sync_elapsed_ms": nfn_sync_ms,
            "nfn_async_elapsed_ms": nfn_async_ms,
            "agentic_async_elapsed_ms": agentic_ms,
            "nfn_sync_result": nfn_sync_result,
            "nfn_async_result": nfn_async_result,
            "agentic_result": agentic_result,
            "wire_bytes_estimate": wire_bytes,
            "interest": "nfn_combine_fanout",
        },
        "artefacts_summary": structural,
    }


async def run_paired(
    *,
    seeds: list[int],
    k_values: list[int],
    out_path: Path,
    baseline: str = "nfn_async",
    include_sync: bool = True,
    include_async: bool = True,
) -> list[dict[str, Any]]:
    """For each seed×k: NFN sync/async + Agentic async + cardiac structural."""
    if baseline not in ("nfn_sync", "nfn_async"):
        raise ValueError(f"baseline must be nfn_sync or nfn_async, got {baseline!r}")
    if baseline == "nfn_sync" and not include_sync:
        raise ValueError("baseline=nfn_sync requires include_sync")
    if baseline == "nfn_async" and not include_async:
        raise ValueError("baseline=nfn_async requires include_async")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with out_path.open("a", encoding="utf-8") as handle:
        for seed in seeds:
            for k in k_values:
                nfn_sync_ms = 0.0
                nfn_sync_result = ""
                nfn_async_ms = 0.0
                nfn_async_result = ""
                wire = 0

                if include_sync:
                    print(f"paired seed={seed} k={k}: NFNForwarder sync…")
                    nfn_sync = await run_nfn_bus(k=k, seed=seed, runtime="sync")
                    nfn_sync_ms = nfn_sync.elapsed_ms
                    nfn_sync_result = nfn_sync.result
                    wire = max(wire, nfn_sync.wire_bytes_estimate)

                if include_async:
                    print(f"paired seed={seed} k={k}: NFNForwarder async…")
                    nfn_async = await run_nfn_bus(k=k, seed=seed, runtime="async")
                    nfn_async_ms = nfn_async.elapsed_ms
                    nfn_async_result = nfn_async.result
                    wire = max(wire, nfn_async.wire_bytes_estimate)

                print(f"paired seed={seed} k={k}: AgenticForwarder async…")
                agentic = await run_agentic_bus(k=k, seed=seed)
                wire = max(wire, agentic.wire_bytes_estimate)

                structural = await _cardiac_structural(k=k, seed=seed)
                record = _comparison_record(
                    seed=seed,
                    k=k,
                    nfn_sync_ms=nfn_sync_ms,
                    nfn_async_ms=nfn_async_ms,
                    agentic_ms=agentic.elapsed_ms,
                    wire_bytes=wire,
                    structural=structural,
                    nfn_sync_result=nfn_sync_result,
                    nfn_async_result=nfn_async_result,
                    agentic_result=agentic.result,
                    baseline=baseline,
                )
                assert record["metrics"]["m3_publishable"] is True
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                records.append(record)
                print(
                    f"  M3 vs {baseline}="
                    f"{record['metrics']['m3_overhead_ratio']:.4f} "
                    f"(publishable); "
                    f"vs_sync={record['metrics']['m3_vs_nfn_sync']}; "
                    f"vs_async={record['metrics']['m3_vs_nfn_async']}"
                )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "SimulationBus: same NFN combine interest under "
            "NFNForwarder sync, NFNForwarder async, and AgenticForwarder async."
        )
    )
    parser.add_argument("--seeds", default="1-10", help="Seeds range or list")
    parser.add_argument("--k", default="3", help="k values (comma or range)")
    parser.add_argument("--out", default=str(_DEFAULT_OUT), help="JSONL output")
    parser.add_argument(
        "--baseline",
        choices=("nfn_async", "nfn_sync"),
        default="nfn_async",
        help="Which plain-NFN timing is the publishable M3 denominator",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip NFNForwarder sync strategy",
    )
    parser.add_argument(
        "--skip-async-nfn",
        action="store_true",
        help="Skip NFNForwarder async strategy",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Accepted for CLI parity; paired bus does not gate on git dirty",
    )
    args = parser.parse_args(argv)
    seeds = parse_int_list(args.seeds)
    k_values = parse_int_list(args.k)
    if not seeds or not k_values:
        parser.error("need at least one seed and one k")
    records = asyncio.run(
        run_paired(
            seeds=seeds,
            k_values=k_values,
            out_path=Path(args.out),
            baseline=args.baseline,
            include_sync=not args.skip_sync,
            include_async=not args.skip_async_nfn,
        )
    )
    print(f"Wrote {len(records)} paired records → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
