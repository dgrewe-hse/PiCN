# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Multi-run measurement campaign → JSONL under ``demo/results/``."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from agentic.benchmark.sweep import (
    SweepSpec,
    parse_int_list,
    parse_str_list,
    run_sweep,
)

_DEFAULT_OUT = Path(__file__).resolve().parent / "results" / "cardiac_structural.jsonl"


async def _async_main(args: argparse.Namespace) -> int:
    seeds = parse_int_list(args.seeds)
    k_values = parse_int_list(args.k)
    paths = parse_str_list(args.path)
    if not seeds:
        raise SystemExit("--seeds must list at least one seed")
    if not k_values:
        raise SystemExit("--k must list at least one value")
    if not paths:
        raise SystemExit("--path must list happy and/or adversary")

    # Default A-011: enough workers for in-process (no real sim interfaces).
    simulated = args.simulated_interfaces
    if args.transport == "bus" and simulated == 0:
        simulated = 0  # capacity gate: workers > interfaces; 0 interfaces → skip refuse

    spec = SweepSpec(
        seeds=seeds,
        k_values=k_values,
        paths=paths,
        transport=args.transport,
        allow_dirty=args.allow_dirty,
        executor_workers=args.executor_workers,
        simulated_interfaces=simulated,
    )
    out = Path(args.out)
    print(
        f"Running {len(spec.configs())} configs → {out} "
        f"(transport={args.transport})"
    )
    results = await run_sweep(spec, out_path=out)
    publishable = sum(1 for r in results if r["metrics"].get("m3_publishable"))
    print(f"Wrote {len(results)} records. m3_publishable runs: {publishable}")
    print(
        "Note: cardiac structural sweeps do not emit publishable "
        "nfn_stack_overhead (legacy m3); use demo.run_paired_bus."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cardiac measurement sweep (default ~50 runs: "
            "seeds 1-5 × k 2,3,4,5,8 × happy,adversary)."
        )
    )
    parser.add_argument(
        "--seeds",
        default="1-5",
        help="Seeds as range (1-5) or list (1,2,3)",
    )
    parser.add_argument(
        "--k",
        default="2,3,4,5,8",
        help="Hospital counts (comma list or range)",
    )
    parser.add_argument(
        "--path",
        default="happy,adversary",
        help="Scenario paths (comma list)",
    )
    parser.add_argument(
        "--transport",
        choices=("bus", "udp"),
        default="bus",
        help="Harness transport metadata label",
    )
    parser.add_argument(
        "--out",
        default=str(_DEFAULT_OUT),
        help="JSONL output path",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permit dirty working trees (stamps non-reproducible)",
    )
    parser.add_argument("--executor-workers", type=int, default=32)
    parser.add_argument("--simulated-interfaces", type=int, default=0)
    args = parser.parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
