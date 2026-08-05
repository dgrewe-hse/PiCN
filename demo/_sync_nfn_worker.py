# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Subprocess worker: run one sync NFN SimulationBus fetch; print JSON."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--log-level", type=int, default=255)
    args = parser.parse_args(argv)
    # Import after argv parse so spawn/fork side effects stay in the worker.
    from demo.bus_topology import run_nfn_bus_sync

    try:
        result = run_nfn_bus_sync(k=args.k, seed=args.seed, log_level=args.log_level)
    except Exception as exc:  # noqa: BLE001
        json.dump({"ok": False, "error": repr(exc)}, sys.stdout)
        print()
        return 1
    json.dump(
        {
            "ok": True,
            "mode": result.mode,
            "elapsed_ms": result.elapsed_ms,
            "result": result.result,
            "k": result.k,
            "seed": result.seed,
            "simulated_interfaces": result.simulated_interfaces,
            "wire_bytes_estimate": result.wire_bytes_estimate,
            "runtime": result.runtime,
        },
        sys.stdout,
    )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
