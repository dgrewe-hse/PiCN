# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Cardiac network demo on SimulationBus (capability Interests)."""

from __future__ import annotations

import json

import pytest

from demo import run_cardiac_bus as run_cardiac_bus_cli
from demo.cardiac_bus_topology import run_cardiac_bus
from demo.run_cardiac_bus import summarize_cardiac_network_records


@pytest.mark.asyncio
async def test_cardiac_network_bus_aggregates_and_ranks() -> None:
    """Ambulance submit_intent → /cap/fwd Interests → Content → ranked choice."""
    result = await run_cardiac_bus(k=2, seed=7, require_paediatric=True, log_level=255)
    assert result.dispatch_count == 5  # enrichment + 2 hospitals + traffic + ranking
    assert result.simulated_interfaces == 2
    assert result.chosen_hospital_id in {"h0", "h1"}
    assert result.extras.get("trace_root_verified") is True
    assert any(n.startswith("/cap/fwd/hospital/beds/") for n in result.sub_intent_names)
    assert len(result.hospital_answers) == 2
    assert result.elapsed_ms >= 0.0


@pytest.mark.asyncio
async def test_cardiac_network_bus_paediatric_filter() -> None:
    """Paediatric filter can exclude a hospital without a team."""
    # seed=2 → h0 paediatric True, h1 paediatric False (see topology formula).
    result = await run_cardiac_bus(
        k=2, seed=2, require_paediatric=True, log_level=255
    )
    assert all(row.get("paediatric_team") for row in result.ranked)
    assert result.chosen_hospital_id is not None


def test_run_cardiac_bus_cli(tmp_path) -> None:
    out = tmp_path / "cardiac.jsonl"
    code = run_cardiac_bus_cli.main(
        ["--k", "2", "--seed", "3", "--out", str(out), "--log-level", "255"]
    )
    assert code == 0
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "cardiac_network_bus" in text
    assert "/cap/fwd" in text or "capability" in text


def test_run_cardiac_bus_sweep_stdev(tmp_path) -> None:
    out = tmp_path / "sweep.jsonl"
    summary = tmp_path / "summary.json"
    code = run_cardiac_bus_cli.main(
        [
            "--seeds",
            "1-3",
            "--k-list",
            "2",
            "--out",
            str(out),
            "--replace-out",
            "--summary-json",
            str(summary),
            "--log-level",
            "255",
        ]
    )
    assert code == 0
    records = [json.loads(line) for line in out.read_text().splitlines() if line]
    assert len(records) == 3
    payload = json.loads(summary.read_text())
    assert payload["runs"] == 3
    assert "stdev" in payload["elapsed_ms"]
    assert payload["elapsed_ms"]["stdev"] >= 0.0
    rolled = summarize_cardiac_network_records(records)
    assert rolled["elapsed_ms"]["n"] == 3.0
