# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""SimulationBus NFN sync/async and AgenticForwarder async comparison."""

from __future__ import annotations

import pytest

from demo.bus_topology import (
    nfn_combine_interest,
    run_agentic_bus,
    run_nfn_bus,
    run_plain_nfn_bus,
)
from demo.run_paired_bus import run_paired


def test_combine_interest_identical_for_k() -> None:
    a = nfn_combine_interest(3)
    b = nfn_combine_interest(3)
    assert a.components == b.components


@pytest.mark.asyncio
async def test_nfn_bus_sync_smoke() -> None:
    result = await run_nfn_bus(k=2, seed=1, runtime="sync")
    assert result.mode == "nfn_sync"
    assert result.runtime == "sync"
    assert result.elapsed_ms > 0
    assert result.result


@pytest.mark.asyncio
async def test_nfn_bus_async_smoke() -> None:
    result = await run_nfn_bus(k=2, seed=1, runtime="async")
    assert result.mode == "nfn_async"
    assert result.runtime == "async"
    assert result.elapsed_ms > 0


@pytest.mark.asyncio
async def test_plain_nfn_alias_is_async() -> None:
    result = await run_plain_nfn_bus(k=2, seed=1)
    assert result.mode == "nfn_async"


@pytest.mark.asyncio
async def test_agentic_bus_smoke() -> None:
    result = await run_agentic_bus(k=2, seed=1)
    assert result.mode == "agentic_async"
    assert result.runtime == "async"
    assert result.elapsed_ms > 0


@pytest.mark.asyncio
async def test_paired_three_strategies_publishable_m3(tmp_path) -> None:
    out = tmp_path / "paired.jsonl"
    records = await run_paired(seeds=[1], k_values=[2], out_path=out)
    assert len(records) == 1
    rec = records[0]
    assert rec["metrics"]["m3_publishable"] is True
    assert rec["metrics"]["m3_overhead_ratio"] is not None
    assert rec["metrics"]["m3_vs_nfn_sync"] is not None
    assert rec["metrics"]["m3_vs_nfn_async"] is not None
    assert rec["bus"]["nfn_sync_elapsed_ms"] > 0
    assert rec["bus"]["nfn_async_elapsed_ms"] > 0
    assert rec["bus"]["agentic_async_elapsed_ms"] > 0
    assert rec["config"]["strategies"] == [
        "nfn_sync",
        "nfn_async",
        "agentic_async",
    ]
    assert out.exists()
