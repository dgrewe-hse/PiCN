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

import asyncio
import contextlib
import socket
from pathlib import Path

import pytest

from demo import physical_node
from demo.physical_topology import (
    PHYSICAL_TRANSPORT,
    build_physical_topology,
    run_physical_cell,
)


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


# --- remote endpoints: the deployed-bench path (same socket semantics) --------


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_bound(port: int, timeout: float = 5.0) -> None:
    """Wait until ``port`` is bound by the edge node (probe bind fails)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"edge node did not bind UDP port {port}")


@pytest.mark.asyncio
async def test_remote_endpoints_build_no_in_process_edges() -> None:
    """With edge endpoints the topology targets remote nodes: it must not
    construct local edge forwarders (the deployed edge owns the socket)."""
    port = _free_udp_port()
    handle = await build_physical_topology(
        k=1,
        edges=1,
        backend="deterministic",
        edge_endpoints=[("127.0.0.1", port)],
        observer_on=False,
    )
    try:
        assert handle.edges == {}
        assert handle.edge_hosts == {"edge1": "127.0.0.1"}
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_remote_endpoint_cell_served_by_deployed_edge(tmp_path: Path) -> None:
    """A cell against a 'remote' edge (real deployed node entrypoint on a
    localhost ephemeral port) serves through the same UDP semantics the
    Raspberry Pi bench uses."""
    port = _free_udp_port()
    config = physical_node.NodeConfig(
        role="edge",
        edge_id="edge1",
        listen_port=port,
        peer_host="",
        peer_port=None,
        owned_prefixes=["/cap/fwd/hospital/beds/h1"],
        backend="deterministic",
        llm_placement=None,
        model_config=None,
        results_dir=str(tmp_path),
        run_id="remote-deployed",
        seed=1,
        k=1,
        leaf_latency_s=0.01,
        edges=1,
    )
    handle = physical_node.write_start_marker(config, host="deployed-edge")
    serve_task = asyncio.create_task(
        physical_node.serve_edge(config, handle, host="deployed-edge")
    )
    try:
        await _wait_bound(port)
        await asyncio.sleep(0.15)  # let the serving stack finish starting
        result = await run_physical_cell(
            run_id="remote-deployed",
            seed=1,
            k=1,
            edges=1,
            leaf_latency_s=0.01,
            backend="deterministic",
            llm_placement=None,
            edge_endpoints=[("127.0.0.1", port)],
        )
    finally:
        serve_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await serve_task
    assert result.per_edge_leaf_counts == {"edge1": 1}
    assert result.leaves[0].served_by_host == "127.0.0.1"
    assert result.leaves[0].transport_ms is not None
    assert result.trace_root_verified is True
