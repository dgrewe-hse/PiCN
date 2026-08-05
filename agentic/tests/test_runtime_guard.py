# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests that agentic nodes refuse the sync runtime."""

from __future__ import annotations

import asyncio

import pytest

from agentic.agentic_layer import AgenticLayer, SyncRuntimeNotSupported, require_async_runtime


def test_require_async_runtime_accepts_async() -> None:
    require_async_runtime("async")  # must not raise


def test_require_async_runtime_rejects_sync() -> None:
    with pytest.raises(SyncRuntimeNotSupported, match="async"):
        require_async_runtime("sync")


def test_agentic_layer_constructor_rejects_sync() -> None:
    with pytest.raises(SyncRuntimeNotSupported):
        AgenticLayer(runtime="sync")


@pytest.mark.asyncio
async def test_agentic_layer_handlers_are_callable_stubs() -> None:
    layer = AgenticLayer(runtime="async")
    q: asyncio.Queue[object] = asyncio.Queue()
    await layer.data_from_lower(q, q, object())
    await layer.data_from_higher(q, q, object())


def test_require_async_runtime_rejects_other_values() -> None:
    with pytest.raises(SyncRuntimeNotSupported):
        require_async_runtime("whatever")
