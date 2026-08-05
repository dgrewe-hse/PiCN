# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Harness metadata gates: seed, transport, dirty tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.benchmark import HarnessError, MeasurementHarness, RunConfig
from agentic.benchmark.metadata import require_run_metadata, working_tree_dirty


def test_refuse_without_seed() -> None:
    with pytest.raises(HarnessError, match="seed"):
        require_run_metadata(
            seed=None,
            transport="bus",
            k=3,
            allow_dirty=True,
            dirty=False,
        )


def test_refuse_without_transport() -> None:
    with pytest.raises(HarnessError, match="transport"):
        require_run_metadata(
            seed=1,
            transport=None,
            k=3,
            allow_dirty=True,
            dirty=False,
        )


def test_refuse_dirty_without_allow() -> None:
    with pytest.raises(HarnessError, match="dirty"):
        require_run_metadata(
            seed=1,
            transport="udp",
            k=2,
            allow_dirty=False,
            dirty=True,
        )


def test_allow_dirty_stamps_non_reproducible() -> None:
    meta = require_run_metadata(
        seed=7,
        transport="bus",
        k=2,
        allow_dirty=True,
        dirty=True,
        commit_hash="abc",
    )
    assert meta.reproducible is False
    assert meta.allow_dirty is True
    assert meta.transport == "bus"
    assert meta.to_dict()["seed"] == 7


def test_harness_prepare_refuses_incomplete(tmp_path: Path) -> None:
    harness = MeasurementHarness(repo_root=tmp_path)
    with pytest.raises(HarnessError, match="seed"):
        harness.prepare(
            RunConfig(
                seed=None,
                transport="bus",
                allow_dirty=True,
                simulated_interfaces=1,
                executor_workers=4,
            )
        )


def test_working_tree_dirty_helper_on_repo() -> None:
    # In this workspace the tree is typically dirty during development; the
    # helper must return a bool without raising.
    assert working_tree_dirty() in (True, False)
