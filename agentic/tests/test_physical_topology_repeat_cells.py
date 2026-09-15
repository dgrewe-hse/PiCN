# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Regression: consecutive ``run_physical_cell`` invocations are independent.

The Experiment E campaign loop calls ``run_physical_cell`` once per cell in a
single process. Each cell must serve **all** ``k`` leaves over real loopback
UDP, every time. A teardown defect that leaves the previous cell's UDP
transports registered with the event loop (or its sockets/face state bound)
must not starve later cells of responses (regression for the campaign defect:
cell 1 served 4/4 leaves; every subsequent cell served 0 leaves and terminated
on the all-NULL trace-root signature).
"""

from __future__ import annotations

import pytest

from demo.physical_topology import (
    MultiEdgePort,
    run_physical_cell,
    split_leaves,
)
from agentic.port.names import Name

_CELLS = 3
_K = 4
_EDGES = 2
_LATENCY_S = 0.02


@pytest.mark.asyncio
async def test_consecutive_cells_each_serve_all_leaves() -> None:
    seen_roots: list[str] = []
    for cell in range(_CELLS):
        result = await run_physical_cell(
            run_id=f"repeat-cells-{cell}",
            seed=1 + cell,
            k=_K,
            edges=_EDGES,
            leaf_latency_s=_LATENCY_S,
            backend="deterministic",
            llm_placement=None,
            observer_on=True,
        )
        # Every cell serves every leaf, every time.
        assert len(result.leaves) == _K, (
            f"cell {cell}: served {len(result.leaves)}/{_K} leaves — "
            f"consecutive cells are not independent"
        )
        assert result.trace_root_verified is True
        # Responses genuinely arrived (transport measured per leaf, and the
        # trace root is not the all-NULL signature the failing cells shared).
        assert all(leaf.transport_ms is not None for leaf in result.leaves)
        seen_roots.append(result.trace_root_hex)
    # An all-NULL teardown collapses every root to the same constant; cells
    # that actually served their leaves produce distinct roots.
    assert len(set(seen_roots)) == _CELLS, (
        f"trace roots repeated across cells: {seen_roots}"
    )


@pytest.mark.asyncio
async def test_cells_independent_without_observer() -> None:
    for cell in range(2):
        result = await run_physical_cell(
            run_id=f"repeat-cells-no-observer-{cell}",
            seed=1 + cell,
            k=_K,
            edges=_EDGES,
            leaf_latency_s=_LATENCY_S,
            backend="deterministic",
            llm_placement=None,
            observer_on=False,
        )
        # Without the observer per-leaf outcomes are not attributed, but the
        # intent must still terminate verified with every edge dispatching.
        assert result.trace_root_verified is True
        assert result.per_edge_leaf_counts == {"edge1": 2, "edge2": 2}
        assert result.elapsed_ms > 0


class _FakePort:
    """Minimal ``SubstratePort`` stand-in for MultiEdgePort delegation."""

    def __init__(self) -> None:
        self.registered: list[Name] = []
        self.unregistered: list[Name] = []

    def parse_name(self, s: str) -> Name:
        return Name(tuple(part.encode("utf-8") for part in s.split("/") if part))

    def name_from_components(self, parts: object) -> Name:
        return Name(tuple(parts))  # type: ignore[arg-type]

    def longest_prefix_match(self, name: Name, table: object) -> None:
        return None

    async def register_prefix(self, prefix: Name, endpoint: object) -> None:
        self.registered.append(prefix)

    async def unregister_prefix(self, prefix: Name, endpoint: object) -> None:
        self.unregistered.append(prefix)

    def lookup(self, name: Name) -> tuple[(),]:
        return ()

    async def start(self, inbound: object) -> None:
        return None

    async def stop(self) -> None:
        return None


def test_split_leaves_rejects_invalid_cells() -> None:
    with pytest.raises(ValueError):
        split_leaves(_K, edges=0)
    with pytest.raises(ValueError):
        split_leaves(k=0, edges=_EDGES)


@pytest.mark.asyncio
async def test_multi_edge_port_routing_surface() -> None:
    fake_a, fake_b = _FakePort(), _FakePort()
    multi = MultiEdgePort(
        {"edge1": ["/a"], "edge2": ["/b"]},
        {"edge1": fake_a, "edge2": fake_b},
    )
    with pytest.raises(ValueError):
        MultiEdgePort({"edge1": ["/a"]}, {"edge1": fake_a, "edge2": fake_b})
    assert multi.leaf_counts == {"edge1": 0, "edge2": 0}
    name_a = multi.parse_name("a")
    assert multi.route(name_a) == "edge1"
    assert multi.route(multi.name_from_components((b"b",))) == "edge2"
    with pytest.raises(ValueError):
        multi.route(Name((b"unknown",)))
    assert multi.longest_prefix_match(name_a, {}) is None
    assert multi.lookup(name_a) == ()
    endpoint = object()
    await multi.register_prefix(name_a, endpoint)  # type: ignore[arg-type]
    assert fake_a.registered == [name_a]
    await multi.unregister_prefix(name_a, endpoint)  # type: ignore[arg-type]
    assert fake_a.unregistered == [name_a]
