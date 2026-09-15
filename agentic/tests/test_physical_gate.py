# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment E: the ``physical_publishable`` gate (design v4 §5.4).

Every gate condition is exercised red/green: UDP transport with >= 8 physical
hosts and per-node run markers, deployed-commit attestation, verified intake
trace root, type-specific per-cell run counts, complete T_intent components
(+ the LLM inference/transport split per leaf), clock-skew accounting, the
name-based prefix-ownership proof, and the NF-5 multi-edge breadth scoping
(deterministic multi-edge cells require >= 2 serving edges; LLM cells are
exempt with ``multi_edge_breadth_required=false``).
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic.benchmark.physical import (
    MIN_LLM_RUNS,
    MIN_PHYSICAL_RUNS,
    PHYSICAL_RUN_KIND,
    build_physical_cell,
    cell_key,
    physical_publishable,
)
from agentic.benchmark.concurrency import CONCURRENCY_RUN_KIND

COMMIT = "a" * 40
_PREFIX_EDGE1 = "/cap/fwd/hospital/beds/h"
_PREFIX_EDGE2 = "/cap/fwd/hospital/beds/h"



def _marker(
    host: str,
    role: str,
    *,
    owned_prefixes: list[str] | None = None,
    deployed: str = COMMIT,
    disk: str = COMMIT,
) -> dict[str, Any]:
    return {
        "host": host,
        "role": role,
        "deployed_commit": deployed,
        "disk_commit": disk,
        "owned_prefixes": owned_prefixes or [],
        "started_at": "2026-09-13T10:00:00Z",
        "ended_at": "2026-09-13T10:05:00Z",
    }


def _markers() -> list[dict[str, Any]]:
    """8-host inventory: intake, 2 edges, 3 producers, 1 llm, 1 observer."""
    return [
        _marker("pi-01", "intake"),
        _marker(
            "pi-02",
            "edge",
            owned_prefixes=[
                f"{_PREFIX_EDGE1}{i}" for i in range(1, 5)
            ],
        ),
        _marker(
            "pi-03",
            "edge",
            owned_prefixes=[
                f"{_PREFIX_EDGE2}{i}" for i in range(5, 9)
            ],
        ),
        _marker("pi-04", "producer"),
        _marker("pi-05", "producer"),
        _marker("pi-06", "producer"),
        _marker("pi-07", "llm"),
        _marker("pi-08", "observer"),
    ]


def _leaf(index: int, *, edge_id: str, host: str) -> dict[str, Any]:
    return {
        "leaf_index": index,
        "name": f"/cap/fwd/hospital/beds/h{index + 1}",
        "edge_id": edge_id,
        "served_by_host": host,
        "inference_ms": None,
        "transport_ms": 3.0,
        "service_ms": 50.0,
    }


def _run(seed: int = 1, **overrides: Any) -> dict[str, Any]:
    """One valid deterministic physical run record."""
    leaves = [ _leaf(i, edge_id="edge1" if i < 4 else "edge2",
                     host="pi-02" if i < 4 else "pi-03")
               for i in range(8) ]
    record: dict[str, Any] = {
        "kind": PHYSICAL_RUN_KIND,
        "record_type": "run",
        "run_id": "e2-det-1",
        "leaves": leaves,
        "seed": seed,
        "k": 8,
        "edges": 2,
        "backend": "deterministic",
        "llm_placement": None,
        "leaf_latency_s": 0.05,
        "transport": "udp",
        "metadata": {
            "transport": "udp",
            "edges": 2,
            "hosts": [f"pi-{i:02d}" for i in range(1, 9)],
            "picn_commit": COMMIT,
            "nature": "physical_deployment",
        },
        "node_markers": _markers(),
        "t_decompose_ms": 1.0,
        "t_dispatch_ms": 8.0,
        "t_network_ms": 3.0,
        "t_service_ms": 50.0,
        "t_aggregate_ms": 2.0,
        "t_intent_ms": 64.0,
        "trace_root_hex": "ab" * 32,
        "trace_root_verified": True,
        "clock": {
            "skew_measured_ms": 0.2,
            "skew_threshold_ms": 1.0,
            "excluded_runs": 0,
        },
        "runs_per_cell_required": MIN_PHYSICAL_RUNS,
    }
    record.update(overrides)
    return record


def _runs(n: int = MIN_PHYSICAL_RUNS, seeds: int = 1) -> list[dict[str, Any]]:
    return [_run(seed=seed) for seed in range(1, seeds + 1) for _ in range(n)]


def _valid_cell(n: int = MIN_PHYSICAL_RUNS) -> dict[str, Any]:
    return build_physical_cell(_runs(n=n))


# --- build_physical_cell -----------------------------------------------------


def test_cell_key_captures_cell_definition() -> None:
    key = cell_key(_run())
    assert key == {
        "seed": 1,
        "k": 8,
        "backend": "deterministic",
        "llm_placement": None,
        "leaf_latency_s": 0.05,
        "edges": 2,
    }


def test_build_cell_reports_stats_and_counts() -> None:
    cell = _valid_cell()
    assert cell["kind"] == PHYSICAL_RUN_KIND
    assert cell["record_type"] == "cell"
    assert cell["n"] == MIN_PHYSICAL_RUNS
    assert cell["median_ms"] == pytest.approx(64.0)
    assert cell["mean_ms"] == pytest.approx(64.0)
    assert cell["stdev_ms"] == pytest.approx(0.0)
    assert cell["metadata"]["transport"] == "udp"
    assert len(cell["metadata"]["hosts"]) == 8
    assert cell["trace_root_verified"] is True
    assert cell["t_intent_components_complete"] is True
    assert cell["multi_edge_breadth_required"] is True
    assert cell["per_edge_leaf_counts"] == {"edge1": 4 * MIN_PHYSICAL_RUNS,
                                            "edge2": 4 * MIN_PHYSICAL_RUNS}


def test_build_cell_refuses_mixed_cells() -> None:
    mixed = _runs(n=2) + [_run(seed=2)]
    with pytest.raises(ValueError):
        build_physical_cell(mixed)
    with pytest.raises(ValueError):
        build_physical_cell([])


def test_build_cell_flags_incomplete_t_intent_components() -> None:
    run = _run()
    run["t_network_ms"] = None
    cell = build_physical_cell([run])
    assert cell["t_intent_components_complete"] is False


# --- the gate ----------------------------------------------------------------


def test_valid_deterministic_multi_edge_cell_is_publishable() -> None:
    assert physical_publishable(_valid_cell()) is True


def test_gate_refuses_wrong_transport() -> None:
    cell = _valid_cell()
    cell["metadata"]["transport"] = "bus"
    assert physical_publishable(cell) is False


def test_gate_requires_eight_distinct_hosts() -> None:
    cell = _valid_cell()
    cell["metadata"]["hosts"] = ["pi-01", "pi-02"]
    assert physical_publishable(cell) is False
    # Distinctness matters, not list length.
    cell["metadata"]["hosts"] = ["pi-01"] * 8
    assert physical_publishable(cell) is False


def test_gate_requires_edges_recorded() -> None:
    cell = _valid_cell()
    cell["metadata"]["edges"] = None
    assert physical_publishable(cell) is False
    cell["metadata"]["edges"] = 0
    assert physical_publishable(cell) is False


def test_gate_requires_node_markers_for_every_host() -> None:
    cell = _valid_cell()
    cell["node_markers"] = cell["node_markers"][:-1]
    assert physical_publishable(cell) is False


def test_gate_requires_deployed_commit_attestation() -> None:
    cell = _valid_cell()
    # Marker's deployed commit must equal the campaign commit on disk.
    cell["node_markers"][1]["disk_commit"] = "b" * 40
    assert physical_publishable(cell) is False
    # Missing attestation entirely also fails.
    cell["node_markers"][1]["deployed_commit"] = ""
    assert physical_publishable(cell) is False
    # And the markers must attest the commit the records claim.
    cell = _valid_cell()
    cell["picn_commit"] = "c" * 40
    assert physical_publishable(cell) is False


def test_gate_requires_verified_trace_root() -> None:
    cell = _valid_cell()
    cell["trace_root_verified"] = False
    assert physical_publishable(cell) is False


def test_gate_requires_minimum_runs_deterministic() -> None:
    assert MIN_PHYSICAL_RUNS >= 30
    cell = _valid_cell(n=MIN_PHYSICAL_RUNS - 1)
    assert physical_publishable(cell) is False


def test_gate_requires_minimum_runs_llm() -> None:
    assert MIN_LLM_RUNS >= 15
    runs = [
        _run(
            backend="llm",
            llm_placement="on-pi",
            leaf_latency_s=None,
            runs_per_cell_required=MIN_LLM_RUNS,
        )
        for _ in range(MIN_LLM_RUNS - 1)
    ]
    for run in runs:
        for leaf in run["leaves"]:
            leaf["inference_ms"] = 12.0
    cell = build_physical_cell(runs)
    # LLM multi-edge cells are exempt from the breadth condition.
    assert cell["multi_edge_breadth_required"] is False
    assert physical_publishable(cell) is False  # n = 14 < 15

    runs.append(runs[0].copy())
    cell = build_physical_cell(runs)
    assert cell["n"] == MIN_LLM_RUNS
    assert physical_publishable(cell) is True


def test_gate_llm_requires_inference_transport_split_per_leaf() -> None:
    runs = [
        _run(
            backend="llm",
            llm_placement="on-pi",
            leaf_latency_s=None,
            runs_per_cell_required=MIN_LLM_RUNS,
        )
        for _ in range(MIN_LLM_RUNS)
    ]
    for run in runs:
        for leaf in run["leaves"]:
            leaf["inference_ms"] = 12.0
            leaf["transport_ms"] = 4.0
    cell = build_physical_cell(runs)
    assert physical_publishable(cell) is True

    runs[0]["leaves"][0]["inference_ms"] = None
    cell = build_physical_cell(runs)
    assert physical_publishable(cell) is False


def test_gate_requires_complete_t_intent_components() -> None:
    cell = _valid_cell()
    cell["t_intent_components_complete"] = False
    assert physical_publishable(cell) is False


def test_gate_requires_skew_below_threshold_and_counted_exclusions() -> None:
    cell = _valid_cell()
    cell["clock"]["skew_measured_ms"] = 1.0
    assert physical_publishable(cell) is True  # at threshold, still below-fail

    cell["clock"]["skew_measured_ms"] = 1.5
    assert physical_publishable(cell) is False

    cell = _valid_cell()
    cell["clock"]["excluded_runs"] = None
    assert physical_publishable(cell) is False


def _ownership_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """Strip the satisfying prefix-ownership proof for negative tests."""
    for leaf in cell["leaves"]:
        leaf["served_by_host"] = "pi-01"  # everything answered by the intake
    return cell


def test_gate_requires_prefix_ownership_proof() -> None:
    cell = _ownership_cell(_valid_cell())
    # A leaf answered by the intake proves nothing about name-based routing.
    assert physical_publishable(cell) is False

    # Answering from a non-intake node that does NOT own the prefix also fails:
    cell = _valid_cell()
    for leaf in cell["leaves"]:
        leaf["served_by_host"] = "pi-08"  # observer owns no prefixes
    assert physical_publishable(cell) is False


def test_gate_deterministic_multi_edge_requires_two_serving_edges() -> None:
    cell = _valid_cell()
    for leaf in cell["leaves"]:
        leaf["edge_id"] = "edge1"
    cell["per_edge_leaf_counts"] = {"edge1": cell["n"]}
    assert physical_publishable(cell) is False


def test_gate_fail_closed_when_breadth_flag_missing() -> None:
    """Collector-merged snapshots can carry ``multi_edge_breadth_required``
    unset (None); the gate must derive the requirement from backend/edges
    (as build_physical_cell does), not skip the breadth check — fail closed."""
    cell = _valid_cell()
    cell["multi_edge_breadth_required"] = None
    for leaf in cell["leaves"]:
        leaf["edge_id"] = "edge1"
    cell["per_edge_leaf_counts"] = {"edge1": cell["n"]}
    assert physical_publishable(cell) is False


def test_gate_derives_breadth_requirement_when_flag_missing() -> None:
    cell = _valid_cell()
    cell["multi_edge_breadth_required"] = None
    assert physical_publishable(cell) is True


def test_gate_llm_cells_exempt_from_multi_edge_breadth() -> None:
    runs = [
        _run(
            backend="llm",
            llm_placement="off-pi",
            leaf_latency_s=None,
            runs_per_cell_required=MIN_LLM_RUNS,
        )
        for _ in range(MIN_LLM_RUNS)
    ]
    for run in runs:
        for leaf in run["leaves"]:
            leaf["inference_ms"] = 9.0
            leaf["transport_ms"] = 2.0
            leaf["edge_id"] = "edge1"  # single-edge serving
    cell = build_physical_cell(runs)
    assert cell["per_edge_leaf_counts"] == {"edge1": 8 * MIN_LLM_RUNS}
    assert cell["multi_edge_breadth_required"] is False
    assert physical_publishable(cell) is True


def test_gate_refuses_non_cell_records() -> None:
    assert physical_publishable(_run()) is False  # a run, not a cell


# --- namespace ---------------------------------------------------------------


def test_physical_run_kind_is_isolated_namespace() -> None:
    assert PHYSICAL_RUN_KIND == "physical_run"
    assert PHYSICAL_RUN_KIND != CONCURRENCY_RUN_KIND
