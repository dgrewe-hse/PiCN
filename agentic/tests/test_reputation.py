# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Property and unit tests for the Beta reputation model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agentic.trust.reputation import (
    ALPHA_0,
    BETA_0,
    DECAY_INTERVAL_S,
    DECAY_LAMBDA,
    MIN_OBSERVATIONS,
    WEIGHT_UNVERIFIED,
    ReputationTable,
)


def test_cold_start_prior_is_uniform() -> None:
    table = ReputationTable()
    mean, var, obs = table.score(b"\x01", "ecg", now=0.0)
    assert obs == 0
    assert mean == pytest.approx(0.5)
    assert 0.0 <= mean <= 1.0
    assert var > 0.0


def test_observations_monotonic_and_never_decayed() -> None:
    table = ReputationTable()
    agent, domain = b"\x02", "beds"
    for i in range(5):
        _, _, obs = table.observe(
            agent, domain, success=True, weight=1.0, now=float(i)
        )
        assert obs == i + 1
    # Long idle then score: observations unchanged.
    _, _, obs_after = table.score(agent, domain, now=1e9)
    assert obs_after == 5


def test_decay_moves_toward_prior() -> None:
    table = ReputationTable()
    agent, domain = b"\x03", "route"
    table.observe(agent, domain, success=True, weight=10.0, now=0.0)
    mean_hot, _, _ = table.score(agent, domain, now=0.0)
    mean_cold, _, _ = table.score(agent, domain, now=100 * DECAY_INTERVAL_S)
    assert mean_hot > 0.5
    assert mean_cold < mean_hot
    assert mean_cold >= 0.5  # toward prior mean 0.5, not away past it from above
    # From failures, decay should raise mean toward 0.5.
    table2 = ReputationTable()
    table2.observe(agent, domain, success=False, weight=10.0, now=0.0)
    mean_bad, _, _ = table2.score(agent, domain, now=0.0)
    mean_recovered, _, _ = table2.score(agent, domain, now=100 * DECAY_INTERVAL_S)
    assert mean_bad < 0.5
    assert mean_recovered > mean_bad
    assert mean_recovered <= 0.5


def test_unverified_weight_weaker_than_attested() -> None:
    strong = ReputationTable()
    weak = ReputationTable()
    strong.observe(b"\x04", "d", success=True, weight=1.0, now=0.0)
    weak.observe(b"\x04", "d", success=True, weight=WEIGHT_UNVERIFIED, now=0.0)
    assert strong.score(b"\x04", "d", 0.0)[0] > weak.score(b"\x04", "d", 0.0)[0]


def test_quarantine_gate() -> None:
    table = ReputationTable()
    assert table.in_quarantine(b"\x05", "d", now=0.0)
    for i in range(MIN_OBSERVATIONS - 1):
        table.observe(b"\x05", "d", success=True, weight=1.0, now=float(i))
        assert table.in_quarantine(b"\x05", "d", now=float(i))
    table.observe(b"\x05", "d", success=True, weight=1.0, now=float(MIN_OBSERVATIONS))
    assert not table.in_quarantine(b"\x05", "d", now=float(MIN_OBSERVATIONS))


def test_atomic_snapshot_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "rep.json"
    table = ReputationTable(path)
    table.observe(b"\xaa", "ecg", success=True, weight=1.0, now=10.0)
    table.observe(b"\xaa", "ecg", success=False, weight=0.3, now=11.0)
    table.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "entries" in raw
    loaded = ReputationTable(path)
    assert loaded.score(b"\xaa", "ecg", now=11.0) == table.score(b"\xaa", "ecg", now=11.0)


def test_as_scorer_for_cfib_seam() -> None:
    table = ReputationTable()
    table.observe(b"\x10" * 16, "cap", success=True, weight=1.0, now=0.0)
    scorer = table.as_scorer("cap")
    assert scorer.score(b"\x10" * 16, now=0.0) == table.score(b"\x10" * 16, "cap", 0.0)[0]


@given(
    successes=st.lists(st.booleans(), min_size=0, max_size=30),
    weight=st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50)
def test_mean_always_in_unit_interval(successes: list[bool], weight: float) -> None:
    table = ReputationTable()
    for i, ok in enumerate(successes):
        mean, var, obs = table.observe(
            "agent", "dom", success=ok, weight=weight, now=float(i)
        )
        assert 0.0 <= mean <= 1.0
        assert var >= 0.0
        assert obs == i + 1


@given(
    n=st.integers(min_value=1, max_value=40),
    pattern=st.lists(st.booleans(), min_size=1, max_size=20),
)
@settings(max_examples=40)
def test_observation_order_independence(n: int, pattern: list[bool]) -> None:
    """Same multiset of outcomes → same posterior regardless of order."""
    # Fix weight and timestamps so only order varies.
    outcomes = (pattern * ((n // len(pattern)) + 1))[:n]
    t1 = ReputationTable()
    t2 = ReputationTable()
    for i, ok in enumerate(outcomes):
        t1.observe("a", "d", success=ok, weight=1.0, now=0.0)
    for i, ok in enumerate(reversed(outcomes)):
        t2.observe("a", "d", success=ok, weight=1.0, now=0.0)
    m1 = t1.score("a", "d", 0.0)
    m2 = t2.score("a", "d", 0.0)
    assert m1[0] == pytest.approx(m2[0])
    assert m1[1] == pytest.approx(m2[1])
    assert m1[2] == m2[2]


@given(n=st.integers(min_value=1, max_value=40))
@settings(max_examples=30)
def test_variance_decreases_with_identical_successes(n: int) -> None:
    table = ReputationTable()
    prev_var = float("inf")
    for i in range(n):
        _, var, obs = table.observe("a", "d", success=True, weight=1.0, now=float(i))
        assert obs == i + 1
        assert var <= prev_var + 1e-12
        prev_var = var


def test_decay_never_moves_away_from_prior_mean() -> None:
    """After positive evidence, decay must not increase mean; after negative, not decrease."""
    table = ReputationTable()
    table.observe("a", "d", success=True, weight=5.0, now=0.0)
    m0, _, _ = table.score("a", "d", 0.0)
    m1, _, _ = table.score("a", "d", DECAY_INTERVAL_S)
    assert m1 <= m0 + 1e-12
    assert abs(m1 - 0.5) <= abs(m0 - 0.5) + 1e-12

    table_neg = ReputationTable()
    table_neg.observe("a", "d", success=False, weight=5.0, now=0.0)
    n0, _, _ = table_neg.score("a", "d", 0.0)
    n1, _, _ = table_neg.score("a", "d", DECAY_INTERVAL_S)
    assert n1 >= n0 - 1e-12
    assert abs(n1 - 0.5) <= abs(n0 - 0.5) + 1e-12


def test_prior_constants_match_beta_one_one() -> None:
    assert ALPHA_0 == 1.0
    assert BETA_0 == 1.0
    assert 0.0 < DECAY_LAMBDA < 1.0
