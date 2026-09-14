# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment E topology over real loopback UDP (no SimulationBus).

Proves the multi-edge intake (one ``PicnSubstratePort`` per edge, directional
LPM prefix ownership) works ``--edges 1`` and ``--edges 2`` without hardware:
each edge is an ``AgenticForwarder`` on an ephemeral localhost UDP port, the
intake fans out leaves by longest-prefix-matched capability ownership, and the
run result carries the honest inference/transport split.
"""

from __future__ import annotations

import pytest

from demo.physical_topology import PHYSICAL_TRANSPORT, run_physical_cell


@pytest.mark.asyncio
async def test_loopback_udp_single_edge_control() -> None:
    result = await run_physical_cell(
        run_id="loopback-e1",
        seed=1,
        k=4,
        edges=1,
        leaf_latency_s=0.01,
        backend="deterministic",
        llm_placement=None,
    )
    assert result.transport == PHYSICAL_TRANSPORT == "udp"
    assert result.edges == 1
    assert result.k == 4
    assert result.elapsed_ms > 0
    assert result.trace_root_verified is True
    # Single edge serves all leaves (the N=1 control cell).
    assert result.per_edge_leaf_counts == {"edge1": 4}
    assert len(result.leaves) == 4
    for leaf in result.leaves:
        assert leaf.edge_id == "edge1"
        assert leaf.transport_ms is not None
        assert leaf.name.startswith("/cap/fwd/hospital/beds/h")
    # Producer-side service time is genuinely measured (non-zero latency).
    assert result.t_service_ms is not None and result.t_service_ms > 0


@pytest.mark.asyncio
async def test_loopback_udp_two_edges_split_leaves() -> None:
    result = await run_physical_cell(
        run_id="loopback-e2",
        seed=2,
        k=8,
        edges=2,
        leaf_latency_s=0.01,
        backend="deterministic",
        llm_placement=None,
    )
    assert result.per_edge_leaf_counts == {"edge1": 4, "edge2": 4}
    by_edge = {leaf.edge_id for leaf in result.leaves}
    assert by_edge == {"edge1", "edge2"}
    # Capability-prefix ownership: h1..h4 → edge1, h5..h8 → edge2.
    for leaf in result.leaves:
        index = leaf.leaf_index
        expected_edge = "edge1" if index < 4 else "edge2"
        assert leaf.edge_id == expected_edge
    assert result.trace_root_verified is True
    # All leaves answered through real UDP transport.
    assert all(leaf.transport_ms is not None for leaf in result.leaves)


@pytest.mark.asyncio
async def test_loopback_udp_rejects_zero_edges() -> None:
    with pytest.raises(ValueError):
        await run_physical_cell(
            run_id="loopback-e0",
            seed=3,
            k=4,
            edges=0,
            leaf_latency_s=0.01,
            backend="deterministic",
            llm_placement=None,
        )


@pytest.mark.asyncio
async def test_loopback_udp_llm_backend_reports_inference_split() -> None:
    result = await run_physical_cell(
        run_id="loopback-llm",
        seed=4,
        k=2,
        edges=1,
        leaf_latency_s=None,
        backend="llm",
        llm_placement="on-pi",
        model_config=None,  # default test preference, offline TestModel
    )
    assert result.backend == "llm"
    assert result.llm_placement == "on-pi"
    for leaf in result.leaves:
        assert leaf.inference_ms is not None
        assert leaf.transport_ms is not None
    assert result.t_service_ms is not None and result.t_service_ms > 0
