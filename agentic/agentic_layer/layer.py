# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Async-only agentic layer sitting above NFN in the stack.

This module subclasses ``AsyncLayerProcess`` directly. There is deliberately
**no** ``AgenticLayerCore``, ``BasicAgenticLayer``, or sync wrapper: the layer
is new code with no sync callers to preserve, and commit-before-forward
ordering, awaited aggregation, and executor dispatch are all async-shaped.
An agentic node must use ``runtime="async"`` — see :func:`require_async_runtime`.

Handlers are stubs until later milestones fill in decomposition and the
Context PIT. Both directions are entry points: local application intents
arrive from higher; capability-named Interests from the network arrive from
lower (NFN must pass non-NFN names upward unmodified).
"""

from __future__ import annotations

import asyncio
from typing import Any

from PiCN.Processes.AsyncLayerProcess import AsyncLayerProcess

from agentic.agentic_layer.runtime import require_async_runtime


class AgenticLayer(AsyncLayerProcess):
    """Topmost async layer for capability routing and bounded decomposition.

    :param log_level: PiCN logger level (255 = quiet).
    :param runtime: Must be ``\"async\"``; sync is refused explicitly.
    """

    def __init__(self, log_level: int = 255, *, runtime: str = "async") -> None:
        require_async_runtime(runtime)
        super().__init__(logger_name="AgenticLayer", log_level=log_level)

    async def data_from_lower(
        self,
        to_lower: asyncio.Queue[Any],
        to_higher: asyncio.Queue[Any],
        data: Any,
    ) -> None:
        """Handle packets arriving from NFN / the network.

        No-op stub: later milestones decompose capability Interests here.
        """
        return None

    async def data_from_higher(
        self,
        to_lower: asyncio.Queue[Any],
        to_higher: asyncio.Queue[Any],
        data: Any,
    ) -> None:
        """Handle intents arriving from the local application.

        No-op stub: later milestones decompose application intents here.
        """
        return None
