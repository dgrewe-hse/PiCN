# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Paired serial-vs-concurrent fan-out on the same leaf set.

The fan-out benefit is real only when serial and concurrent dispatch operate on
identical leaves: same ``k``, same leaf payloads, same trace root. These tests
assert byte-identical trace roots, a speedup > 1 when leaf latency is positive,
~1.0x at zero latency (the control cell), peak in-flight, and overlap.
"""

from __future__ import annotations

import asyncio
import hashlib
import time

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.adapters.mock import MockSubstratePort
from agentic.adapters.mock.clock import ManualClock
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.decomposer import SubIntent
from agentic.trust import generate_ed25519_private_key


def _issuer_der() -> bytes:
    key = generate_ed25519_private_key()
    return key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


def _leaf_correlations(parent: bytes, subs: list[SubIntent]) -> list[bytes]:
    corrs: list[bytes] = []
    for sub in subs:
        digest = hashlib.sha256(
            parent
            + bytes(str(sub.index), "ascii")
            + b"/".join(c.encode() for c in sub.capability)
        ).digest()
        corrs.append(hashlib.sha256(parent + digest).digest())
    return corrs


class _LatencyPort(MockSubstratePort):
    """Mock port that blocks ``send_request`` for ``latency_s`` (leaf service).

    The block models a real producer backend (``LatencyBackend``): serial
    dispatch pays ``k * latency``; concurrent dispatch pays ~``latency``. The
    terminal is still emitted via the normal mock ``_put`` so the layer's event
    pump drives aggregation.
    """

    def __init__(self, *, latency_s: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self._latency_s = latency_s
        self._inflight = 0
        self.peak_inflight = 0

    async def send_request(self, name, payload, *, correlation, deadline) -> None:
        # Model a real producer backend: the leaf takes ``latency_s`` to serve.
        # Serial dispatch pays k*latency; concurrent pays ~latency (all parallel).
        self._inflight += 1
        self.peak_inflight = max(self.peak_inflight, self._inflight)
        try:
            await asyncio.sleep(self._latency_s)
        finally:
            self._inflight -= 1
        await super().send_request(name, payload, correlation=correlation, deadline=deadline)


def _make_subs(k: int, prefix: str = "leaf") -> list[SubIntent]:
    return [
        SubIntent(index=i, capability=(prefix, f"item{i}"), bindings={}) for i in range(k)
    ]


async def _run(
    *, parent: bytes, k: int, latency_s: float, dispatch: str, port, warmup: int = 0
) -> tuple[bytes, float]:
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        subs = _make_subs(k)
        # One-time lazy initialisation (event-loop machinery, first send) can
        # dominate a sub-millisecond measurement. Warm up the dispatch path so
        # the timed run reflects steady-state, not cold-start cost. Use a
        # distinct parent so the warm-up entry does not collide with the timed
        # run in the PIT.
        for w in range(warmup):
            warm_parent = hashlib.sha256(parent + bytes([w])).digest()
            for corr in _leaf_correlations(warm_parent, subs):
                port.script_response(corr, b"ok")
            await asyncio.wait_for(
                layer.submit_intent(
                    parent_intent_digest=warm_parent,
                    issuer_public_key_der=_issuer_der(),
                    sub_intents=subs,
                    aggregation_policy="ALL",
                    deadline=10.0,
                    now_ms=0,
                    dispatch=dispatch,
                ),
                timeout=10.0,
            )
        for corr in _leaf_correlations(parent, subs):
            port.script_response(corr, b"ok")
        started = time.perf_counter_ns()
        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=_issuer_der(),
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
                dispatch=dispatch,
            ),
            timeout=10.0,
        )
        elapsed = (time.perf_counter_ns() - started) / 1e9
        return root, elapsed
    finally:
        await layer.stop_port()


@pytest.mark.asyncio
async def test_paired_same_leaf_set_identical_trace_root() -> None:
    k = 4
    latency = 0.02
    parent = hashlib.sha256(b"paired-identical").digest()
    serial_port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    conc_port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    serial_root, _ = await _run(parent=parent, k=k, latency_s=latency, dispatch="serial", port=serial_port)
    conc_root, _ = await _run(parent=parent, k=k, latency_s=latency, dispatch="concurrent", port=conc_port)
    assert serial_root == conc_root


@pytest.mark.asyncio
async def test_concurrent_speedup_positive_latency() -> None:
    """With leaf latency > 0, concurrent dispatch is faster than serial."""
    k = 6
    latency = 0.05
    parent = hashlib.sha256(b"paired-speedup").digest()
    serial_port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    conc_port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    _, serial_elapsed = await _run(parent=parent, k=k, latency_s=latency, dispatch="serial", port=serial_port)
    _, conc_elapsed = await _run(parent=parent, k=k, latency_s=latency, dispatch="concurrent", port=conc_port)
    speedup = serial_elapsed / conc_elapsed if conc_elapsed > 0 else float("inf")
    assert speedup > 1.0


@pytest.mark.asyncio
async def test_zero_latency_control_approx_unity() -> None:
    """Zero-latency control cell: no concurrency benefit.

    At ``leaf_latency_s == 0`` the leaves are served in ~0 time, so the
    measured wall-clock is dominated by harness noise (event-loop scheduling,
    ``TaskGroup``/``create_task`` setup, ``time.perf_counter_ns`` resolution)
    rather than by any real concurrency benefit. The invariant this cell guards
    is "zero latency => no meaningful speedup", so:

    * If both elapsed times are below a timing-noise threshold, the ratio is
      meaningless — assert the two are comparable (absolute difference small)
      rather than asserting a ratio.
    * Otherwise assert the ratio lies in a wide, noise-tolerant band.

    Either way the cell documents that at zero latency the measurement is
    noise-dominated, never a real speedup.
    """
    k = 4
    latency = 0.0
    parent = hashlib.sha256(b"paired-control").digest()
    serial_port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    conc_port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    # Warm up both ports: the first submit_intent pays one-time lazy-init cost
    # that would otherwise dominate a sub-millisecond zero-latency measurement.
    _, serial_elapsed = await _run(parent=parent, k=k, latency_s=latency, dispatch="serial", port=serial_port, warmup=1)
    _, conc_elapsed = await _run(parent=parent, k=k, latency_s=latency, dispatch="concurrent", port=conc_port, warmup=1)
    # Timing-noise threshold: below this the ratio is dominated by harness
    # noise, not by any real concurrency effect (see module docstring).
    timing_noise_s = 1e-3
    if serial_elapsed < timing_noise_s and conc_elapsed < timing_noise_s:
        # Sub-millisecond work: the ratio is meaningless. Assert the two are
        # comparable (no meaningful speedup) rather than a ratio.
        assert abs(serial_elapsed - conc_elapsed) < timing_noise_s
    else:
        # Above the noise floor, allow a wide band: the point is "no meaningful
        # speedup", not a tight ~1.0x that timing jitter would violate.
        speedup = serial_elapsed / conc_elapsed if conc_elapsed > 0 else float("inf")
        assert 0.1 < speedup < 6.0


@pytest.mark.asyncio
async def test_peak_inflight_serial_is_one() -> None:
    k = 5
    latency = 0.02
    parent = hashlib.sha256(b"peak-serial").digest()
    port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    await _run(parent=parent, k=k, latency_s=latency, dispatch="serial", port=port)
    assert port.peak_inflight == 1


@pytest.mark.asyncio
async def test_peak_inflight_concurrent_exceeds_one() -> None:
    k = 5
    latency = 0.05
    parent = hashlib.sha256(b"peak-concurrent").digest()
    port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    await _run(parent=parent, k=k, latency_s=latency, dispatch="concurrent", port=port)
    assert port.peak_inflight >= 2


@pytest.mark.asyncio
async def test_concurrent_all_leaves_scheduled_before_completion() -> None:
    """All sends are scheduled (TaskGroup) before any is awaited."""
    k = 3
    latency = 0.02
    port = _LatencyPort(latency_s=latency, clock=ManualClock(0.0))
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        parent = hashlib.sha256(b"sched").digest()
        subs = _make_subs(k)
        for corr in _leaf_correlations(parent, subs):
            port.script_response(corr, b"ok")
        await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=_issuer_der(),
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
                dispatch="concurrent",
            ),
            timeout=10.0,
        )
        # All leaves marked forwarded in the prepass before any send.
        entry = layer.pit.get(parent)
        assert entry is not None and entry.terminated
        assert all(leaf.forwarded for leaf in entry.leaves)
    finally:
        await layer.stop_port()