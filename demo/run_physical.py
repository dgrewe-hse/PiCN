# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""CLI: Experiment E — physical testbed over UDP (design v4 §5.3).

Runs concurrent fan-out cells against the multi-edge physical topology
(:mod:`demo.physical_topology`), one ``kind="physical_run"`` record per run.
Records carry the T_intent decomposition plus, for the LLM backend, the
``inference_ms``/``transport_ms`` split per leaf — model inference is **never**
attributed to the overlay.

**Cost preflight** (A-011 style — refuse loudly): the projected wall-clock
(``runs_per_cell × cells × per-run estimate`` from the design v4 §5.3 cost
matrix) is printed before any campaign; a projection over ``--max-hours``
refuses to start unless ``--ack-cost`` is passed.

Namespace: this runner writes only ``kind="physical_run"`` records and never
reads the ``concurrency_run`` namespace (design v4 §5.5 — D and E numbers
never share a table, figure, or derived ratio).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agentic.benchmark.metadata import _git_head, working_tree_dirty
from agentic.benchmark.physical import PHYSICAL_RUN_KIND, PUBLISHABLE_TRANSPORT
from agentic.benchmark.sweep import parse_int_list, parse_str_list
# demo.physical_topology must be imported before agentic.binding: on a cold
# interpreter, agentic.binding pulls agentic.agentic_layer via the registry,
# whose producer re-imports the partially initialized agentic.binding.registry.
from demo.physical_topology import run_physical_cell
from agentic.binding.pydantic_ai_backend import ModelConfigError, load_model_preference

_DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "results" / "physical"

# Namespace contract (design v4 §5.5): this runner writes exactly this kind
# and never touches kind="concurrency_run" (Experiment D).
_EXPECTED_KIND_LITERALS: frozenset[str] = frozenset({"physical_run"})

# Cost-matrix per-run estimates (design v4 §5.3): E-DET < 1 s; E-LLM-A
# (on-Pi Ollama) 3–30 s; E-LLM-C (off-Pi GPU) 1–10 s. The upper bounds are
# used so the projection errs on the expensive side.
_DET_BASELINE_S = 0.5
_LLM_ON_PI_S = 30.0
_LLM_OFF_PI_S = 10.0

_DEFAULT_MAX_HOURS = 6.0
_DEFAULT_SKEW_THRESHOLD_MS = 1.0

_EXIT_OK = 0
_EXIT_COST_REFUSED = 3


def _parse_float_list(raw: str) -> list[float]:
    return [float(x) for x in parse_str_list(raw)]


def _cell_count(seeds: int, k_values: int, leaf_latencies: int, backend: str) -> int:
    return seeds * k_values * (leaf_latencies if backend != "llm" else 1)


def _per_run_estimate(
    backend: str, llm_placement: str, leaf_latencies: list[float]
) -> float:
    if backend == "llm":
        return _LLM_ON_PI_S if llm_placement == "on-pi" else _LLM_OFF_PI_S
    return _DET_BASELINE_S + (max(leaf_latencies) if leaf_latencies else 0.0)


def _preflight(
    *,
    backend: str,
    llm_placement: str,
    leaf_latencies: list[float],
    seeds: int,
    k_values: int,
    runs_per_cell: int,
    max_hours: float,
    ack_cost: bool,
) -> float:
    """Print the projected wall-clock; refuse campaigns over ``max_hours``.

    :return: The projected total seconds.
    """
    cells = _cell_count(seeds, k_values, len(leaf_latencies), backend)
    per_run_s = _per_run_estimate(backend, llm_placement, leaf_latencies)
    total_s = per_run_s * cells * runs_per_cell
    print(
        f"Cost preflight: {cells} cells x {runs_per_cell} runs/cell "
        f"x ~{per_run_s:.2f}s/run -> ~{total_s:.0f}s ({total_s / 3600.0:.2f} h) "
        f"[backend={backend}, placement={llm_placement}]"
    )
    if total_s > max_hours * 3600.0 and not ack_cost:
        print(
            f"refusing to start: projected {total_s / 3600.0:.2f} h exceeds "
            f"--max-hours {max_hours:.2f} h; pass --ack-cost to override"
        )
    return total_s


def _run_record(
    result: Any,
    *,
    seed: int,
    k: int,
    runs_per_cell: int,
    picn_commit: str | None,
) -> dict[str, Any]:
    backend = result.backend
    return {
        "kind": PHYSICAL_RUN_KIND,
        "record_type": "run",
        "run_id": result.run_id,
        "seed": seed,
        "k": k,
        "edges": result.edges,
        "backend": backend,
        "llm_placement": result.llm_placement,
        # LLM cells drop --leaf-latency-s (Finding 9): inference dominates.
        "leaf_latency_s": result.leaf_latency_s,
        "transport": PUBLISHABLE_TRANSPORT,
        "dispatch": result.dispatch,
        "metadata": {
            "transport": PUBLISHABLE_TRANSPORT,
            "edges": result.edges,
            "hosts": result.hosts,
            "picn_commit": picn_commit,
            "nature": "physical_deployment",
        },
        "elapsed_ms": result.elapsed_ms,
        "trace_root_hex": result.trace_root_hex,
        "trace_root_verified": result.trace_root_verified,
        "t_decompose_ms": result.t_decompose_ms,
        "t_dispatch_ms": result.t_dispatch_ms,
        "t_network_ms": result.t_network_ms,
        # Producer-side service time (inference for LLM), clock-offset field:
        # loopback runs share one clock (offset 0); a deployment applies the
        # collector's measured offset before the gate consumes these records.
        "t_service_ms": result.t_service_ms,
        "t_aggregate_ms": result.t_aggregate_ms,
        "t_intent_ms": result.t_intent_ms,
        "leaves": [
            {
                "leaf_index": leaf.leaf_index,
                "name": leaf.name,
                "edge_id": leaf.edge_id,
                "served_by_host": leaf.served_by_host,
                "inference_ms": leaf.inference_ms,
                "transport_ms": leaf.transport_ms,
                "service_ms": leaf.service_ms,
            }
            for leaf in result.leaves
        ],
        "per_edge_leaf_counts": result.per_edge_leaf_counts,
        "clock": {
            "skew_measured_ms": 0.0 if result.clock_offset_ms == 0.0 else result.clock_offset_ms,
            "skew_threshold_ms": _DEFAULT_SKEW_THRESHOLD_MS,
            "excluded_runs": 0,
            "skew_basis": "single_clock_loopback" if result.clock_offset_ms == 0.0 else "collector_corrected",
        },
        "clock_offset_ms": result.clock_offset_ms,
        "runs_per_cell_required": runs_per_cell,
    }


async def _async_main(args: argparse.Namespace) -> int:
    seeds = parse_int_list(args.seeds)
    k_values = parse_int_list(args.k)
    if not seeds:
        raise SystemExit("--seeds must list at least one seed")
    if not k_values:
        raise SystemExit("--k must list at least one value")
    if args.edges < 1:
        raise SystemExit(f"--edges must be >= 1, got {args.edges}")

    leaf_latencies = _parse_float_list(args.leaf_latency_s) \
        if args.backend != "llm" else [None]  # type: ignore[list-item]
    if args.backend == "deterministic":
        if not leaf_latencies:
            raise SystemExit("--leaf-latency-s must list at least one value")
    if args.backend == "llm" and args.llm_placement not in ("on-pi", "off-pi"):
        raise SystemExit("--llm-placement must be on-pi or off-pi for LLM cells")

    # Cost preflight (A-011): refuse loudly before starting anything.
    projected_s = _preflight(
        backend=args.backend,
        llm_placement=args.llm_placement,
        leaf_latencies=[lat for lat in leaf_latencies if lat is not None],
        seeds=len(seeds),
        k_values=len(k_values),
        runs_per_cell=args.runs_per_cell,
        max_hours=args.max_hours,
        ack_cost=args.ack_cost,
    )
    if projected_s > args.max_hours * 3600.0 and not args.ack_cost:
        return _EXIT_COST_REFUSED
    if args.dry_run:
        print("dry-run complete; no campaign started")
        return _EXIT_OK

    # Model config is an LLM-only concern: deterministic cells never load it.
    if args.backend == "llm":
        try:
            load_model_preference(args.model_config)
        except ModelConfigError as exc:
            raise SystemExit(f"model config unusable: {exc}") from exc

    # A-011 style reproducibility gate: refuse a dirty tree unless allowed.
    if working_tree_dirty(cwd=None) and not args.allow_dirty:
        raise SystemExit(
            "refusing to run from a dirty working tree without --allow-dirty"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_count = 0
    with out.open("w", encoding="utf-8") as handle:
        for seed in seeds:
            for k in k_values:
                for leaf_latency_s in leaf_latencies:
                    result = await run_physical_cell(
                        run_id=args.run_id,
                        seed=seed,
                        k=k,
                        edges=args.edges,
                        leaf_latency_s=leaf_latency_s,
                        backend=args.backend,
                        llm_placement=(
                            args.llm_placement if args.backend == "llm" else None
                        ),
                        model_config=args.model_config if args.backend == "llm" else None,
                        observer_on=args.observer == "on",
                    )
                    record = _run_record(
                        result,
                        seed=seed,
                        k=k,
                        runs_per_cell=args.runs_per_cell,
                        picn_commit=_git_head(),
                    )
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                    handle.flush()
                    run_count += 1
                    print(
                        f"  seed={seed} k={k} edges={args.edges} "
                        f"t_intent_ms={record['t_intent_ms']:.1f} "
                        f"leaves={len(record['leaves'])}"
                    )

    print(f"Wrote {run_count} {PHYSICAL_RUN_KIND} records -> {out}")
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Experiment E: physical multi-edge testbed over UDP. Concurrent "
            "fan-out cells with one PicnSubstratePort per edge; writes only "
            "kind=\"physical_run\" records (namespace-isolated from "
            "Experiment D)."
        )
    )
    parser.add_argument(
        "--backend", choices=("deterministic", "llm"), default="deterministic",
        help="Producer backend: deterministic or staged LLM (design v4 §5.3)",
    )
    parser.add_argument(
        "--llm-placement", choices=("on-pi", "off-pi"), default="on-pi",
        help="LLM stage A (on-Pi Ollama) or stage C (off-Pi GPU host)",
    )
    parser.add_argument("--edges", type=int, default=2, help="Edge count N (>= 1)")
    parser.add_argument("--seeds", default="1-5", help="Seeds (range or list)")
    parser.add_argument("--k", default="8", help="Leaf counts (comma/range)")
    parser.add_argument(
        "--leaf-latency-s", default="0.05",
        help="Per-leaf deterministic latency (seconds, comma list); dropped "
        "from LLM cells (Finding 9)",
    )
    parser.add_argument("--runs-per-cell", type=int, default=30)
    parser.add_argument(
        "--model-config", default="",
        help="Model preference TOML (LLM backend only; never read for "
        "deterministic cells)",
    )
    parser.add_argument(
        "--transport", choices=("udp",), default="udp",
        help="Physical transport (Experiment E is UDP-only)",
    )
    parser.add_argument("--run-id", default="", help="Campaign run id")
    parser.add_argument("--out", default="", help="JSONL output path")
    parser.add_argument(
        "--max-hours", type=float, default=_DEFAULT_MAX_HOURS,
        help="Refuse campaigns whose cost-preflight projection exceeds this",
    )
    parser.add_argument(
        "--ack-cost", action="store_true",
        help="Override a cost-preflight refusal",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the cost projection and exit without running the campaign",
    )
    parser.add_argument("--observer", choices=("on", "off"), default="on")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--log-level", type=int, default=255)
    args = parser.parse_args(argv)
    if not args.run_id:
        args.run_id = f"physical-{int(__import__('time').time())}"
    if not args.out:
        args.out = str(_DEFAULT_OUT_DIR / f"{args.run_id}.jsonl")
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
