# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""End-to-end cardiac scenario: happy path and adversary sub-case."""

from __future__ import annotations

import pytest

from agentic.adapters.mock import MockSubstratePort
from agentic.scenario import Scenario, available_scenarios
from agentic.scenario.cardiac import CardiacScenario


@pytest.mark.asyncio
async def test_cardiac_happy_path_trace_root_and_contributions() -> None:
    scenario = CardiacScenario(k=3, seed=1)
    assert isinstance(scenario, Scenario)
    port = MockSubstratePort()
    await scenario.setup(port=port)
    try:
        result = await scenario.run_happy_path(patient_id="p-1")
        assert result["path"] == "happy"
        assert result["trace_root_verified"] is True
        assert result["accountability_log_length"] > 0
        assert len(result["contributions"]) == 3 + 3  # enrichment + k=3 + traffic + ranking
        # Expected set size is fixed at commit (I2); every leaf resolved (not NULL).
        assert len(result["contributions"]) == 6
        assert all(c["null"] is False for c in result["contributions"])
        assert result["intake_outcome"]["accepted"] is True
        assert len(result["hospital_responses"]) == 3
        for response in result["hospital_responses"]:
            assert "quote_id" in response
    finally:
        await scenario.teardown()


@pytest.mark.asyncio
async def test_cardiac_adversary_kpa_deprioritises_not_bans() -> None:
    scenario = CardiacScenario(k=3, seed=2)
    await scenario.setup()
    try:
        result = await scenario.run_adversary(patient_id="p-adv")
        assert result["path"] == "adversary"
        assert result["quote_valid_with_false_claim"] is True
        assert result["inflated_claim_beds"] > result["true_beds"]
        assert result["kpa_mismatches"]
        assert any(
            m["hospital_id"] == result["adversary_id"] for m in result["kpa_mismatches"]
        )
        assert result["reputation_after_mismatch"]["mean"] < 0.5
        assert result["still_eligible"] is True
        assert result["adversary_rank_index"] > 0
        assert result["trace_root_verified_first"] is True
        assert result["trace_root_verified_second"] is True
        # Inflated claim was hashed into the first exchange like any other response.
        first_quotes = {
            r["quote_id"] for r in result["first_exchange"]["hospital_responses"]
        }
        assert len(first_quotes) == 3
    finally:
        await scenario.teardown()


def test_cardiac_registered_as_pluggable_scenario() -> None:
    # Importing the plugin populates the registry.
    import agentic.scenario.cardiac  # noqa: F401

    registered = available_scenarios()
    assert "cardiac-response" in registered
    assert registered["cardiac-response"] is CardiacScenario


def test_scenario_package_has_no_transport_branching() -> None:
    """AC: scenario code must not branch on bus vs udp."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "scenario"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("transport ==", "is_bus", "use_udp"):
            if needle in text:
                offenders.append(f"{path}:{needle}")
    assert offenders == []
