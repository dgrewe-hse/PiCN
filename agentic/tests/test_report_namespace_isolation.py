# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Namespace isolation between the D and E report generators (design v4 §5.5).

The hard rule — D and E numbers never share a table, figure, or derived ratio
— is enforced by an **AST check on each generator's own source**: the kind
literals it references must be exactly its own namespace's kind, checked
against the full known-kind set (non-self-referential). The functional load
path must also filter records by the generator's own kind.
"""

from __future__ import annotations

import json
from pathlib import Path

import demo.generate_concurrency_report as d_report
import demo.generate_physical_report as e_report

_REPO = Path(__file__).resolve().parents[2]


def _load(module: object) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]


def test_physical_report_references_only_physical_run() -> None:
    source = _load(e_report)
    kinds = e_report._referenced_kinds(source)
    assert kinds == {"physical_run"}


def test_concurrency_report_references_only_concurrency_run() -> None:
    source = _load(d_report)
    kinds = d_report._referenced_kinds(source)
    assert kinds == {"concurrency_run"}


def test_namespace_check_is_non_self_referential() -> None:
    """The scan must consider the full known-kind set, not the expected set.

    Passing the *other* generator's source through a generator's scan must
    report the other kind (proving the scan is not pre-filtered to the
    expected set), while still yielding exactly the expected set for its own
    source.
    """
    other = d_report._referenced_kinds(_load(e_report))
    assert other == {"physical_run"}
    own = e_report._referenced_kinds(_load(d_report))
    assert own == {"concurrency_run"}


def test_physical_report_load_filters_namespace(tmp_path: Path) -> None:
    mixed = tmp_path / "mixed.jsonl"
    run = _minimal_physical_run()
    with mixed.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "concurrency_run", "seed": 1}) + "\n")
        handle.write(json.dumps(run) + "\n")
    loaded = e_report._load_records(mixed)
    assert len(loaded) == 1
    assert loaded[0]["kind"] == "physical_run"


def _minimal_physical_run() -> dict:
    """A small valid physical_run record (shape, not gate-completeness)."""
    return {
        "kind": "physical_run",
        "record_type": "run",
        "run_id": "r",
        "seed": 1,
        "k": 1,
        "edges": 1,
        "backend": "deterministic",
        "llm_placement": None,
        "leaf_latency_s": 0.01,
        "transport": "udp",
        "metadata": {"transport": "udp", "edges": 1, "hosts": ["h"], "picn_commit": "x"},
        "t_intent_ms": 1.0,
    }


def test_physical_report_mandatory_caption(tmp_path: Path, capsys) -> None:
    from agentic.benchmark.physical import build_physical_cell

    # Reuse the gate suite's valid-cell construction for a realistic report.
    import sys

    sys.path.insert(0, str(_REPO / "agentic" / "tests"))
    try:
        from test_physical_gate import _runs, COMMIT  # noqa: PLC0415
    finally:
        sys.path.pop(0)

    cell = build_physical_cell(_runs(n=30))
    jsonl = tmp_path / "physical.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(cell) + "\n")

    report_dir = tmp_path / "report"
    code = e_report.main(["--jsonl", str(jsonl), "--report-dir", str(report_dir)])
    assert code == 0
    report = (report_dir / "physical_evaluation.md").read_text(encoding="utf-8")
    # Mandatory caption (design v4 §5.5): N hosts, commit, honest framing.
    assert (
        "Physical deployment on 8 Raspberry Pi 5 nodes over UDP at commit "
        f"{COMMIT}"
    ) in report
    assert "measured wall-clock includes real transport" in report
    assert "model inference, reported separately" in report
    assert "clock-offset-corrected" in report
    # Per-cell statistics table.
    assert "| 30 |" in report
