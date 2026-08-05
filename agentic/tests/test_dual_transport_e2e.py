# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Dual-transport scenario runs and M3 same-seed reproducibility (G.3)."""

from __future__ import annotations

import pytest

from agentic.adapters.mock import MockSubstratePort
from agentic.benchmark import MeasurementHarness, RunConfig
from agentic.scenario.cardiac import CardiacScenario
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime
from agentic.agentic_layer.runtime import SyncRuntimeNotSupported


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["bus", "udp"])
async def test_cardiac_happy_path_both_transports(transport: str) -> None:
    """Scenario stays transport-agnostic; harness stamps transport metadata."""
    scenario = CardiacScenario(k=2, seed=11)
    port = MockSubstratePort()
    await scenario.setup(port=port)
    try:
        result = await scenario.run_happy_path(patient_id=f"p-{transport}")
        assert result["trace_root_verified"] is True
        assert result["path"] == "happy"
    finally:
        await scenario.teardown()

    harness = MeasurementHarness()
    out = await harness.run(
        RunConfig(
            seed=11,
            transport=transport,
            k=2,
            allow_dirty=True,
            executor_workers=8,
            simulated_interfaces=2,
            path="happy",
        )
    )
    assert out["metadata"]["transport"] == transport
    assert out["metrics"]["m3_overhead_ratio"] is not None


@pytest.mark.asyncio
async def test_m3_overhead_reproducible_same_seed() -> None:
    """M3 ratio is reproducible across two runs with the same seed+transport."""
    harness_a = MeasurementHarness()
    harness_b = MeasurementHarness()
    cfg = RunConfig(
        seed=99,
        transport="bus",
        k=2,
        allow_dirty=True,
        executor_workers=8,
        simulated_interfaces=2,
        path="happy",
    )
    a = await harness_a.run(cfg)
    b = await harness_b.run(cfg)
    # Same seed → same scenario structure; M3 is agentic/nfn within one run.
    # Ratios should match closely (synthetic baseline is a fixed fraction).
    assert a["metrics"]["m3_overhead_ratio"] == pytest.approx(
        b["metrics"]["m3_overhead_ratio"], rel=1e-9, abs=1e-9
    )
    assert a["metadata"]["seed"] == b["metadata"]["seed"] == 99


@pytest.mark.asyncio
async def test_agentic_forwarder_starts_async_only() -> None:
    with pytest.raises(SyncRuntimeNotSupported):
        AgenticForwarder(port=0, runtime=Runtime.SYNC, log_level=255)

    fwd = AgenticForwarder(port=0, runtime=Runtime.ASYNC, log_level=255)
    await fwd.start_forwarder_async()
    try:
        assert fwd.agentic is not None
        assert fwd.runtime is Runtime.ASYNC
    finally:
        await fwd.stop_forwarder_async()
        for iface in fwd.interfaces:
            iface.close()
