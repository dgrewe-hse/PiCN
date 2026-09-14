# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""``LatencyBackend`` — a ``CapabilityBackend`` wrapper with controllable latency.

Wraps an inner backend and inserts a controllable ``asyncio.sleep`` before
(and, for jitter, around) delegating. This is what makes ``T_service`` a real,
non-zero measurement in Experiment D: the producer registers a ``LatencyBackend``
so ``hub.invoke`` blocks for ``latency_s`` before returning.

* ``jitter_s == 0`` — deterministic (Experiment D).
* ``jitter_s > 0`` — seeded random (Experiment E); the same seed reproduces the
  same jitter sequence, so cells remain comparable across runs.

No ``PiCN.*`` and no LLM imports here (AC1/AC2/AC3): this is a pure wrapper.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from typing import Any

from agentic.binding.protocol import CapabilityBackend

Clock = Callable[[], float]


def _default_clock() -> float:
    import time

    return time.perf_counter()


class LatencyBackend:
    """Wrap a ``CapabilityBackend`` to add a controllable service latency.

    :param inner: The wrapped backend (deterministic, pydantic-ai, …).
    :param latency_s: Base service latency in seconds (``>= 0``).
    :param jitter_s: Optional seeded jitter amplitude (``>= 0``; 0 = deterministic).
    :param seed: RNG seed for jitter (reproducible when ``jitter_s > 0``).
    :param clock: Injected monotonic clock for ``measured`` accounting.
    :param measured: Whether service time is genuinely measured (``True``).
    """

    def __init__(
        self,
        inner: CapabilityBackend,
        *,
        latency_s: float,
        jitter_s: float = 0.0,
        seed: int | None = None,
        clock: Clock | None = None,
        measured: bool = True,
    ) -> None:
        if latency_s < 0:
            raise ValueError(f"latency_s must be >= 0, got {latency_s!r}")
        if jitter_s < 0:
            raise ValueError(f"jitter_s must be >= 0, got {jitter_s!r}")
        self._inner = inner
        self._latency_s = latency_s
        self._jitter_s = jitter_s
        self._clock = clock if clock is not None else _default_clock
        self.measured = measured
        self._jitter_sampler = random.Random(seed) if jitter_s > 0 else None

    @property
    def inner(self) -> CapabilityBackend:
        """The wrapped backend (delegation target)."""
        return self._inner

    @property
    def latency_s(self) -> float:
        """Base service latency in seconds."""
        return self._latency_s

    def declared_schemas(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the inner backend's declared ``(input, output)`` schemas."""
        return self._inner.declared_schemas()

    def _next_jitter(self) -> float:
        """Sample one jitter offset (``0`` when deterministic)."""
        if self._jitter_sampler is None:
            return 0.0
        return self._jitter_sampler.uniform(-self._jitter_s, self._jitter_s)

    async def invoke(
        self, payload: dict[str, Any], *, deadline: float
    ) -> dict[str, Any]:
        """Sleep for ``latency_s (+ jitter)``, then delegate to the inner backend.

        The sleep models real producer service time so ``T_service`` is a
        genuine measurement (never zero when ``latency_s > 0``).
        """
        jitter = self._next_jitter()
        delay = max(0.0, self._latency_s + jitter)
        if delay > 0:
            await asyncio.sleep(delay)
        return await self._inner.invoke(payload, deadline=deadline)


__all__ = ["Clock", "LatencyBackend"]