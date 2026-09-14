# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment E: physical-run snapshot and the ``physical_publishable`` gate.

This module is pure Python — it never imports ``PiCN.*`` (AC1) and never
imports an LLM client (AC3). It aggregates per-run ``physical_run`` records
into per-cell snapshots and derives the ``physical_publishable`` gate
(design v4 §5.4).

The namespace is Experiment E's alone: only ``kind="physical_run"`` records
are consumed here, never ``kind="concurrency_run"`` (design v4 §5.5 — D and E
numbers never share a table, figure, or derived ratio).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from agentic.benchmark.concurrency import CONCURRENCY_RUN_KIND

# The exact record kind this snapshot / gate layer consumes.
PHYSICAL_RUN_KIND = "physical_run"

# Every kind tag used anywhere in the D/E experiment layer. Report generators
# scan their own source against this set (non-self-referential namespace check).
KNOWN_RUN_KINDS = frozenset({CONCURRENCY_RUN_KIND, PHYSICAL_RUN_KIND})

# The transport on which a physical-deployment claim is publishable.
PUBLISHABLE_TRANSPORT = "udp"

# Type-specific per-cell run-count thresholds (design v4 §5.3/§5.4):
# deterministic cells are cheap (n >= 30); LLM cells are inference-dominated
# (n >= 15, disclosed in captions).
MIN_PHYSICAL_RUNS = 30
MIN_LLM_RUNS = 15

# Record-type discriminator inside the ``physical_run`` namespace.
RECORD_TYPE_RUN = "run"
RECORD_TYPE_CELL = "cell"

# The five T_intent decomposition components + the total wall-clock.
_T_INTENT_COMPONENTS = (
    "t_decompose_ms",
    "t_dispatch_ms",
    "t_network_ms",
    "t_service_ms",
    "t_aggregate_ms",
)

_CELL_KEY_FIELDS = (
    "seed",
    "k",
    "backend",
    "llm_placement",
    "leaf_latency_s",
    "edges",
)


def cell_key(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the cell-definition key of a run record.

    Two runs share a cell only when every field — ``(seed, k, backend,
    llm_placement, leaf_latency_s, edges)`` — matches exactly.

    :param record: A ``physical_run`` run record.
    :return: The cell key mapping.
    """
    return {field: record.get(field) for field in _CELL_KEY_FIELDS}


def _marker_host(markers: Sequence[Mapping[str, Any]], host: str) -> dict[str, Any] | None:
    for marker in markers:
        if marker.get("host") == host:
            return dict(marker)
    return None


def _owns_prefix(owned: Sequence[str], name: str) -> bool:
    """True when ``name`` falls under a component-boundary prefix in ``owned``."""
    for prefix in owned:
        if name == prefix or name.startswith(prefix if prefix.endswith("/") else prefix + "/"):
            # Component-boundary check: "/h1" must not match "/h10".
            if name.startswith(prefix) and (
                len(name) == len(prefix) or name[len(prefix) : len(prefix) + 1] == "/"
            ):
                return True
    return False


def _min_required_runs(record: Mapping[str, Any]) -> int:
    backend = record.get("backend")
    return MIN_LLM_RUNS if backend == "llm" else MIN_PHYSICAL_RUNS


def build_physical_cell(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate same-cell run records into one cell snapshot.

    :param runs: Per-run ``physical_run`` records (>= 1) sharing a cell key.
    :return: A cell snapshot with ``n``/median/mean/stdev, merged leaves,
        node markers, per-edge leaf counts, and gate-facing booleans.
    :raises ValueError: When ``runs`` is empty or mixes cell definitions.
    """
    if not runs:
        raise ValueError("a physical cell requires at least one run record")
    first_key = cell_key(runs[0])
    for run in runs[1:]:
        if cell_key(run) != first_key:
            raise ValueError(
                "physical cell mixes cell definitions "
                f"({first_key} vs {cell_key(run)})"
            )

    # Cell key completeness (design v4 §5.3): llm_placement is a real value
    # only for LLM cells, and leaf_latency_s is dropped from LLM cells —
    # those Nones are legitimate, everything else must be recorded.
    required_key_fields = ("seed", "k", "backend", "edges")
    if any(first_key[field] is None for field in required_key_fields):
        raise ValueError(f"incomplete cell key: {first_key}")
    if first_key["backend"] == "llm" and first_key["llm_placement"] is None:
        raise ValueError(f"incomplete cell key (llm_placement): {first_key}")

    run_list = [dict(run) for run in runs]
    intent_values = [
        float(run["t_intent_ms"])
        for run in run_list
        if run.get("t_intent_ms") is not None
    ]
    n = len(run_list)
    median_ms = statistics.median(intent_values) if intent_values else 0.0
    mean_ms = statistics.fmean(intent_values) if intent_values else 0.0
    stdev_ms = statistics.stdev(intent_values) if len(intent_values) > 1 else 0.0

    # Metadata agreement: every run must report the same deployment fingerprint.
    hosts: list[str] = list(runs[0].get("metadata", {}).get("hosts", []))
    commit = runs[0].get("metadata", {}).get("picn_commit")
    transport = runs[0].get("metadata", {}).get("transport")
    edges = first_key["edges"]
    for run in run_list[1:]:
        meta = run.get("metadata", {})
        if (
            sorted(map(str, meta.get("hosts", []))) != sorted(map(str, hosts))
            or meta.get("picn_commit") != commit
            or meta.get("transport") != transport
            or run.get("edges") != edges
        ):
            raise ValueError("physical cell runs disagree on deployment metadata")

    # Node markers merged by host (collector-attached; missing here fails the
    # gate's condition 1/2, it never mutes the report).
    markers: dict[str, dict[str, Any]] = {}
    for run in run_list:
        for marker in run.get("node_markers", []) or []:
            markers[str(marker.get("host"))] = dict(marker)

    # Merged leaves + per-edge leaf counts (condition 7/8 evidence).
    leaves: list[dict[str, Any]] = []
    per_edge: dict[str, int] = {}
    for run in run_list:
        for leaf in run.get("leaves", []) or []:
            leaves.append(dict(leaf))
            edge_id = str(leaf.get("edge_id"))
            per_edge[edge_id] = per_edge.get(edge_id, 0) + 1

    # Condition 5: every run reports all five components + t_intent.
    t_intent_complete = bool(run_list) and all(
        all(run.get(component) is not None for component in _T_INTENT_COMPONENTS)
        and run.get("t_intent_ms") is not None
        for run in run_list
    )

    # Condition 3: every run's intake submit_intent verified its trace root.
    trace_root_verified = bool(run_list) and all(
        run.get("trace_root_verified") is True for run in run_list
    )

    # Condition 6: residual clock skew, worst across runs; exclusions counted.
    skews = [
        float(run["clock"]["skew_measured_ms"])
        for run in run_list
        if isinstance(run.get("clock"), Mapping)
        and run["clock"].get("skew_measured_ms") is not None
    ]
    thresholds = [
        float(run["clock"]["skew_threshold_ms"])
        for run in run_list
        if isinstance(run.get("clock"), Mapping)
        and run["clock"].get("skew_threshold_ms") is not None
    ]
    excluded = [
        int(run["clock"]["excluded_runs"])
        for run in run_list
        if isinstance(run.get("clock"), Mapping)
        and run["clock"].get("excluded_runs") is not None
    ]

    backend = first_key["backend"]
    edges_count = int(first_key["edges"] or 0)
    # NF-5 scoping: breadth required only for deterministic multi-edge cells.
    multi_edge_required = backend == "deterministic" and edges_count >= 2

    cell: dict[str, Any] = {
        "kind": PHYSICAL_RUN_KIND,
        "record_type": RECORD_TYPE_CELL,
        "run_id": run_list[0].get("run_id"),
        "cell_key": dict(first_key),
        "seed": first_key["seed"],
        "k": first_key["k"],
        "backend": backend,
        "llm_placement": first_key["llm_placement"],
        "leaf_latency_s": first_key["leaf_latency_s"],
        "edges": edges_count,
        "n": n,
        "runs_per_cell_required": run_list[0].get("runs_per_cell_required"),
        "median_ms": median_ms,
        "mean_ms": mean_ms,
        "stdev_ms": stdev_ms,
        "metadata": {
            "transport": transport,
            "edges": edges_count,
            "hosts": hosts,
            "picn_commit": commit,
            "nature": "physical_deployment",
        },
        "picn_commit": commit,
        "transport": transport,
        "node_markers": list(markers.values()),
        "leaves": leaves,
        "per_edge_leaf_counts": per_edge,
        "trace_root_verified": trace_root_verified,
        "t_intent_components_complete": t_intent_complete,
        "multi_edge_breadth_required": multi_edge_required,
        "clock": {
            "skew_measured_ms": max(skews) if skews else None,
            "skew_threshold_ms": thresholds[0] if thresholds else None,
            "excluded_runs": sum(excluded) if excluded else None,
        },
        "run_ids": sorted({str(run.get("run_id")) for run in run_list}),
    }
    return cell


def physical_publishable(cell: Mapping[str, Any]) -> bool:
    """Return True iff the cell satisfies the ``physical_publishable`` gate.

    Implements design v4 §5.4 conditions 1–8. Any failure — a non-UDP
    transport, fewer than 8 attested physical hosts, a missing or mismatched
    deployed-commit marker, an unverified trace root, an insufficient or
    type-inconsistent run count, an incomplete T_intent decomposition (or a
    missing LLM inference/transport split), unrecorded or over-threshold clock
    skew, a leaf answered without a non-intake prefix-ownership proof, or a
    deterministic multi-edge cell served by fewer than 2 edges — yields
    ``False``; the record is retained for audit.

    :param cell: A cell snapshot from :func:`build_physical_cell`.
    """
    # Condition 0 (namespace): gate applies to cell snapshots only.
    if cell.get("kind") != PHYSICAL_RUN_KIND or cell.get("record_type") != RECORD_TYPE_CELL:
        return False

    metadata = cell.get("metadata")
    if not isinstance(metadata, Mapping):
        return False

    # Condition 1: UDP transport, --edges recorded, >= 8 distinct physical
    # hosts in the inventory, and a run marker for every node.
    if metadata.get("transport") != PUBLISHABLE_TRANSPORT:
        return False
    edges = metadata.get("edges")
    if not isinstance(edges, int) or isinstance(edges, bool) or edges < 1:
        return False
    hosts = metadata.get("hosts")
    if not isinstance(hosts, Sequence) or isinstance(hosts, (str, bytes)):
        return False
    distinct_hosts = {str(host) for host in hosts}
    if len(distinct_hosts) < 8:
        return False
    markers: list[Mapping[str, Any]] = list(cell.get("node_markers") or [])
    marker_hosts = {str(marker.get("host")) for marker in markers}
    if not distinct_hosts <= marker_hosts:
        return False

    # Condition 2: deployed-commit attestation. Every node's marker must
    # record the commit actually deployed == the commit on disk == the
    # campaign commit. A clean local tree is not sufficient.
    commit = cell.get("picn_commit") or metadata.get("picn_commit")
    if not isinstance(commit, str) or not commit:
        return False
    for marker in markers:
        deployed = marker.get("deployed_commit")
        disk = marker.get("disk_commit")
        if not isinstance(deployed, str) or not deployed:
            return False
        if deployed != disk or deployed != commit:
            return False

    # Condition 3: intake submit_intent completed with verified trace root.
    if cell.get("trace_root_verified") is not True:
        return False

    # Condition 4: per-cell run count meets the type-specific threshold for
    # the *same* cell key (seed, k, backend, llm_placement, leaf_latency_s,
    # edges); the report carries n/median/mean/stdev.
    n = cell.get("n")
    required = cell.get("runs_per_cell_required")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return False
    if not isinstance(required, int) or isinstance(required, bool):
        return False
    backend = cell.get("backend")
    min_required = MIN_LLM_RUNS if backend == "llm" else MIN_PHYSICAL_RUNS
    if required < min_required or n < required:
        return False
    stats_present = all(
        isinstance(cell.get(stat), (int, float)) and not isinstance(cell.get(stat), bool)
        for stat in ("median_ms", "mean_ms", "stdev_ms")
    )
    if not stats_present:
        return False

    # Condition 5: all five T_intent components present per run (and the
    # total); LLM cells additionally require the inference/transport split
    # per leaf — inference is never credited to the overlay.
    if cell.get("t_intent_components_complete") is not True:
        return False
    leaves: Sequence[Mapping[str, Any]] = list(cell.get("leaves") or [])
    if not leaves:
        return False
    if backend == "llm":
        for leaf in leaves:
            if leaf.get("inference_ms") is None or leaf.get("transport_ms") is None:
                return False

    # Condition 6: host clock skew measured, recorded, below threshold;
    # exclusions counted.
    clock = cell.get("clock")
    if not isinstance(clock, Mapping):
        return False
    skew = clock.get("skew_measured_ms")
    threshold = clock.get("skew_threshold_ms")
    excluded_runs = clock.get("excluded_runs")
    if not isinstance(skew, (int, float)) or isinstance(skew, bool):
        return False
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        return False
    if skew > threshold:
        return False
    if not isinstance(excluded_runs, int) or isinstance(excluded_runs, bool) or excluded_runs < 0:
        return False

    # Condition 7: name-based routing proof. At least one leaf must be
    # answered by a producer whose declared capability prefix is owned by a
    # non-intake node, attested in that node's own run marker.
    ownership_proved = False
    for leaf in leaves:
        owner = _marker_host(markers, str(leaf.get("served_by_host")))
        if owner is None or owner.get("role") == "intake":
            continue
        if owner.get("deployed_commit") != commit:
            continue
        owned = owner.get("owned_prefixes") or []
        if _owns_prefix([str(prefix) for prefix in owned], str(leaf.get("name"))):
            ownership_proved = True
            break
    if not ownership_proved:
        return False

    # Condition 8 (NF-5): multi-edge breadth is required for deterministic
    # multi-edge cells; >= 2 edges must have served at least one leaf each.
    # LLM cells are exempt (recorded ``multi_edge_breadth_required=false``) —
    # their claim is substrate-share under inference, not fan-out breadth.
    if cell.get("multi_edge_breadth_required") is True:
        per_edge: Mapping[str, Any] = cell.get("per_edge_leaf_counts") or {}
        serving_edges = sum(
            1 for count in per_edge.values() if isinstance(count, int) and count >= 1
        )
        if serving_edges < 2:
            return False

    return True


__all__ = [
    "KNOWN_RUN_KINDS",
    "MIN_LLM_RUNS",
    "MIN_PHYSICAL_RUNS",
    "PHYSICAL_RUN_KIND",
    "PUBLISHABLE_TRANSPORT",
    "RECORD_TYPE_CELL",
    "RECORD_TYPE_RUN",
    "build_physical_cell",
    "cell_key",
    "physical_publishable",
]
