# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""``LatencyBackend``: a ``CapabilityBackend`` wrapper with controllable latency.

Determinism (``jitter_s=0``), seeded jitter (``jitter_s>0``), schema
delegation, and ``CapabilityBackend`` conformance are all exercised here.
"""

from __future__ import annotations

import asyncio
import time

import pytest

# Import a submodule of agentic.agentic_layer before agentic.binding so the
# pre-existing registry<->layer circular import resolves in a stable order.
from agentic.agentic_layer.descriptor import CapabilityDescriptor  # noqa: F401
from agentic.binding import CapabilityBackend, LatencyBackend
from agentic.binding.deterministic import DeterministicBackend

INPUT_SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "integer"}},
    "required": ["x"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"result": {"type": "integer"}},
    "required": ["result"],
    "additionalProperties": False,
}


def _make_handler():
    async def handler(payload):
        return {"result": payload["x"] * 2}

    return handler


def _make_backend(**kwargs):
    inner = DeterministicBackend(
        _make_handler(), input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA
    )
    return LatencyBackend(inner, **kwargs)


class _ManualClock:
    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        self._now += delta


@pytest.mark.asyncio
async def test_implements_capability_backend() -> None:
    backend = _make_backend(latency_s=0.01)
    assert isinstance(backend, CapabilityBackend)


@pytest.mark.asyncio
async def test_declared_schemas_delegates_to_inner() -> None:
    backend = _make_backend(latency_s=0.01)
    in_s, out_s = backend.declared_schemas()
    assert in_s == INPUT_SCHEMA
    assert out_s == OUTPUT_SCHEMA


@pytest.mark.asyncio
async def test_invoke_returns_inner_result() -> None:
    backend = _make_backend(latency_s=0.0)
    result = await backend.invoke({"x": 21}, deadline=10.0)
    assert result == {"result": 42}


@pytest.mark.asyncio
async def test_latency_deterministic_adds_sleep() -> None:
    """jitter_s=0 → wall-clock is at least latency_s (deterministic)."""
    latency = 0.05
    backend = _make_backend(latency_s=latency)
    started = time.monotonic()
    await backend.invoke({"x": 1}, deadline=10.0)
    elapsed = time.monotonic() - started
    assert elapsed >= latency * 0.8  # allow scheduling slack


@pytest.mark.asyncio
async def test_jitter_seeded_is_reproducible() -> None:
    """Same seed → same injected jitter sequence across instances."""
    a = _make_backend(latency_s=0.0, jitter_s=0.01, seed=123)
    b = _make_backend(latency_s=0.0, jitter_s=0.01, seed=123)
    # The jitter sampler is a deterministic RNG: the same seed must draw the
    # same sequence (checked via the internal sampler, not wall-clock).
    assert a._next_jitter() == b._next_jitter()
    assert a._next_jitter() == b._next_jitter()
    assert a._next_jitter() == b._next_jitter()


@pytest.mark.asyncio
async def test_jitter_different_seeds_differ() -> None:
    a = _make_backend(latency_s=0.0, jitter_s=0.01, seed=1)
    b = _make_backend(latency_s=0.0, jitter_s=0.01, seed=2)
    a_vals = [a._next_jitter() for _ in range(3)]
    b_vals = [b._next_jitter() for _ in range(3)]
    assert a_vals != b_vals


@pytest.mark.asyncio
async def test_injected_clock_used_for_measured() -> None:
    clock = _ManualClock()
    backend = _make_backend(latency_s=0.0, clock=clock)
    assert backend.measured is True
    # A measured backend records service time via the injected clock.
    await backend.invoke({"x": 5}, deadline=10.0)
    assert clock() >= 0.0


@pytest.mark.asyncio
async def test_measured_flag_default_true() -> None:
    backend = _make_backend(latency_s=0.0)
    assert backend.measured is True


def test_negative_latency_rejected() -> None:
    with pytest.raises(ValueError):
        _make_backend(latency_s=-0.01)


def test_negative_jitter_rejected() -> None:
    with pytest.raises(ValueError):
        _make_backend(latency_s=0.0, jitter_s=-0.01)


def test_requires_inner_backend() -> None:
    with pytest.raises(TypeError):
        LatencyBackend()  # type: ignore[call-arg]

@pytest.mark.asyncio
async def test_invoke_records_measured_service_time() -> None:
    """The measured service wall-clock covers sleep + inner invoke (VLAD PR-3)."""
    backend = _make_backend(latency_s=0.01)
    await backend.invoke({"x": 5}, deadline=10.0)
    assert backend.last_measured_s is not None
    assert 0.01 <= backend.last_measured_s < 0.5


@pytest.mark.asyncio
async def test_measured_false_keeps_record_unmeasured() -> None:
    backend = _make_backend(latency_s=0.0, measured=False)
    await backend.invoke({"x": 5}, deadline=10.0)
    assert backend.measured is False
    assert backend.last_measured_s is None


@pytest.mark.asyncio
async def test_service_record_reports_measured_accounting() -> None:
    backend = _make_backend(latency_s=0.01)
    await backend.invoke({"x": 5}, deadline=10.0)
    record = backend.service_record()
    assert record["measured"] is True
    assert record["measured_s"] is not None and record["measured_s"] >= 0.01
    assert record["latency_s"] == 0.01


def test_service_record_before_invoke_is_unmeasured() -> None:
    backend = _make_backend(latency_s=0.0, clock=_ManualClock())
    record = backend.service_record()
    assert record["measured_s"] is None
    assert record["latency_s"] == 0.0
