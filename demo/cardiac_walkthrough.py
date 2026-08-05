# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Narrative cardiac walkthrough — happy path and adversary (no LLM)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from agentic.scenario.cardiac import CardiacScenario


def _print_banner() -> None:
    print("=== Agentic cardiac walkthrough ===")
    print("No live LLM required (DeterministicBackend).")
    print()


async def run_walkthrough(*, k: int, seed: int, patient_id: str) -> int:
    """Run happy + adversary and print key artefacts.

    :return: Process exit code (0 on verified roots).
    """
    _print_banner()
    scenario = CardiacScenario(k=k, seed=seed)
    await scenario.setup()
    try:
        happy = await scenario.run_happy_path(patient_id=patient_id)
        print("--- Happy path ---")
        print(f"  k={k} seed={seed}")
        print(f"  trace_root_verified={happy['trace_root_verified']}")
        print(f"  leaf_count={len(happy['contributions'])}")
        print(f"  context_pit_peak={happy['context_pit_peak']}")
        print(f"  dispatch_count={happy['dispatch_count']}")
        print(f"  aggregation_complete={happy['aggregation_complete']}")
        print(f"  artefact_bytes={happy['artefact_bytes']}")
        print(f"  top_hospital={happy['ranking'][0]['hospital_id'][:16]}…")
        print(f"  intake={json.dumps(happy['intake_outcome'], sort_keys=True)}")
        print(f"  trace_root={happy['trace_root'][:32]}…")
        print()

        adv = await scenario.run_adversary(patient_id=f"{patient_id}-adv")
        print("--- Adversary path ---")
        print(f"  quote_valid_with_false_claim={adv['quote_valid_with_false_claim']}")
        print(f"  kpa_mismatches={len(adv['kpa_mismatches'])}")
        print(f"  reputation_after={adv['reputation_after_mismatch']}")
        print(f"  adversary_rank_index={adv['adversary_rank_index']}")
        print(f"  still_eligible={adv['still_eligible']}")
        print(f"  context_pit_peak={adv['context_pit_peak']}")
        print(f"  dispatch_count={adv['dispatch_count']}")
        print()

        ok = (
            happy["trace_root_verified"]
            and adv["trace_root_verified_first"]
            and adv["trace_root_verified_second"]
            and adv["adversary_rank_index"] > 0
        )
        if ok:
            print("Walkthrough OK.")
            return 0
        print("Walkthrough FAILED verification checks.", file=sys.stderr)
        return 1
    finally:
        await scenario.teardown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cardiac-response walkthrough (in-process, no LLM)."
    )
    parser.add_argument("--k", type=int, default=3, help="Hospital count (>=2)")
    parser.add_argument("--seed", type=int, default=42, help="Scenario seed")
    parser.add_argument("--patient-id", default="demo-patient", help="Patient id")
    args = parser.parse_args(argv)
    if args.k < 2:
        parser.error("--k must be >= 2")
    return asyncio.run(
        run_walkthrough(k=args.k, seed=args.seed, patient_id=args.patient_id)
    )


if __name__ == "__main__":
    raise SystemExit(main())
