# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Collector: consolidate producer-side service records (Experiment E).

Gathers the per-node observer service records from every node's results dir
(``service_records/*.json``, one array of records per producing host) and
applies the clock-offset correction (design v4 §5.6): the producer-measured
``service_measured_ms`` is corrected into the intake clock domain by the
collector-measured per-host ``clock_offset_ms``:

    service_corrected_ms = service_measured_ms - clock_offset_ms

Workers' clocks are disciplined with chrony; the residual offset of each
producer against the intake reference is measured during bring-up
(``runbooks/clock_sync.md``) and recorded with every service record.

Records whose residual ``|clock_offset_ms|`` exceeds ``--skew-threshold-ms``
are **flagged** (``skew_exceeded``) and counted — never silently dropped — so
the gate's condition 6 (``clock.excluded_runs``) and the report can disclose
the exclusion. The residual skew of the consolidated set is the worst
absolute offset.

Optionally, per-node run markers (``markers/<run-id>-<host>.json``, written by
:mod:`demo.physical_node`) are merged into the campaign JSONL's
``kind="physical_run"`` records as ``node_markers`` — the attachment the
``physical_publishable`` gate consumes (conditions 1/2/7).

Pure stdlib; reads only the results-tree namespace of Experiment E.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

# Service-record fields, in the collector's unit (ms). The design's ns unit
# (t_service_ns / clock_offset_ns, design v2 §"Producer-side t_service") is
# accepted and normalised for backwards compatibility with early node images
# (both the t_service_ns and the service_measured_ns spellings).
_MS_FIELDS = ("service_measured_ms", "clock_offset_ms")
_NS_TO_MS_FIELDS = {
    "t_service_ns": "service_measured_ms",
    "service_measured_ns": "service_measured_ms",
    "clock_offset_ns": "clock_offset_ms",
}

DEFAULT_SKEW_THRESHOLD_MS = 1.0

_EXIT_OK = 0
_EXIT_USAGE = 2


def _normalise_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one service record to milliseconds.

    Copies every field, converting ``t_service_ns``/``clock_offset_ns`` to
    their ``*_ms`` counterparts when the ms fields are absent.
    """
    record = dict(raw)
    for ns_field, ms_field in _NS_TO_MS_FIELDS.items():
        if ms_field not in record and ns_field in record:
            record[ms_field] = float(record[ns_field]) / 1_000_000.0
    return record


def _load_service_records(results_dir: Path) -> list[dict[str, Any]]:
    """Load every service record from ``<results_dir>/service_records/*.json``."""
    records: list[dict[str, Any]] = []
    srv_dir = results_dir / "service_records"
    for path in sorted(srv_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries: Sequence[Mapping[str, Any]] = (
            payload if isinstance(payload, list) else [payload]
        )
        for entry in entries:
            records.append(_normalise_record(entry))
    return records


def load_service_records(results_dirs: Sequence[Path]) -> list[dict[str, Any]]:
    """All service records from every given node results dir, stable order."""
    records: list[dict[str, Any]] = []
    for results_dir in results_dirs:
        records.extend(_load_service_records(results_dir))
    return records


def residual_skew_ms(records: Sequence[Mapping[str, Any]]) -> float:
    """Worst absolute clock offset (ms) across ``records``; 0.0 when empty.

    This is the *residual* skew after chrony bring-up: what remains between
    the intake clock domain and each producer's domain at measurement time.
    """
    return max(
        (abs(float(record.get("clock_offset_ms", 0.0))) for record in records),
        default=0.0,
    )


def correct_record(record: Mapping[str, Any], *, skew_threshold_ms: float) -> dict[str, Any]:
    """Return the corrected copy of one service record.

    ``service_corrected_ms`` moves the producer measurement into the intake
    clock domain; ``skew_exceeded`` marks records beyond the threshold.
    """
    measured = float(record.get("service_measured_ms", 0.0))
    offset = float(record.get("clock_offset_ms", 0.0))
    corrected = dict(record)
    corrected["service_measured_ms"] = measured
    corrected["clock_offset_ms"] = offset
    corrected["service_corrected_ms"] = measured - offset
    corrected["skew_exceeded"] = abs(offset) > skew_threshold_ms
    return corrected


def _find_markers(results_dirs: Sequence[Path], run_id: str) -> list[dict[str, Any]]:
    """Markers for ``run_id``: ``markers/<run-id>-<host>.json`` per node."""
    markers: list[dict[str, Any]] = []
    prefix = f"{run_id}-"
    for results_dir in results_dirs:
        markers_dir = results_dir / "markers"
        for path in sorted(markers_dir.glob(f"{prefix}*.json")):
            marker = json.loads(path.read_text(encoding="utf-8"))
            marker.setdefault("host", path.name[len(prefix):-len(".json")])
            markers.append(marker)
    return markers


def merge_markers(
    jsonl_path: Path,
    results_dirs: Sequence[Path],
    *,
    run_id: str | None,
) -> int:
    """Attach ``node_markers`` to every matching ``physical_run`` record.

    A record is matched by its ``run_id`` (or by ``--run-id`` when given);
    marker files are located by the ``<run-id>-<host>.json`` naming convention.
    Records without markers are left untouched — the gate treats missing
    markers as a failure, never mutes them here.

    :return: Number of run records that received markers.
    """
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    merged_count = 0
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("kind") == "physical_run" and record.get("record_type") == "run":
                target = run_id or record.get("run_id")
                if target:
                    markers = _find_markers(results_dirs, str(target))
                    if markers:
                        record["node_markers"] = markers
                        merged_count += 1
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return merged_count


def main(argv: list[str] | None = None) -> int:
    """Collector CLI: consolidate, correct, and (optionally) attach markers."""
    parser = argparse.ArgumentParser(
        description=(
            "Experiment E service-record collector: gather producer-side "
            "observer records, apply the clock-offset correction into the "
            "intake clock domain, and flag/record residual skew above "
            "threshold."
        )
    )
    parser.add_argument(
        "--results-dir",
        action="extend",
        nargs="+",
        required=True,
        metavar="DIR",
        help="Node results root(s) (rsync'd or shared); repeatable",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Consolidated corrected JSONL output path",
    )
    parser.add_argument(
        "--merge-into",
        default="",
        help="Campaign JSONL whose kind=physical_run records receive the "
        "per-node run markers as node_markers",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Only collect records/markers for this run id",
    )
    parser.add_argument(
        "--skew-threshold-ms",
        type=float,
        default=DEFAULT_SKEW_THRESHOLD_MS,
        help="Residual clock-offset threshold (ms) above which records are "
        "flagged skew_exceeded and counted as excluded",
    )
    args = parser.parse_args(argv)
    if not args.out and not args.merge_into:
        parser.error("needs --out and/or --merge-into")

    results_dirs = [Path(d) for d in args.results_dir]
    records = load_service_records(results_dirs)
    if args.run_id:
        records = [r for r in records if r.get("run_id") == args.run_id]
    records.sort(
        key=lambda r: (
            str(r.get("run_id", "")),
            str(r.get("host", "")),
            int(r.get("leaf_index", 0)),
        )
    )

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        corrected = [
            correct_record(r, skew_threshold_ms=args.skew_threshold_ms)
            for r in records
        ]
        with out.open("w", encoding="utf-8") as handle:
            for record in corrected:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        exceeded = sum(1 for record in corrected if record["skew_exceeded"])
        print(
            f"collected {len(corrected)} service record(s) from "
            f"{len(results_dirs)} result dir(s); residual skew "
            f"{residual_skew_ms(records):.3f} ms; {exceeded} above "
            f"threshold {args.skew_threshold_ms:.3f} ms -> {out}"
        )

    if args.merge_into:
        merged = merge_markers(
            Path(args.merge_into), results_dirs, run_id=args.run_id or None
        )
        print(f"attached node markers to {merged} physical_run record(s)")

    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
