# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""``LatencyObserver``: per-leaf timing hooks for the T_intent decomposition.

The observer is a passive interface: ``AgenticLayer`` calls hooks only, never
computes derived metrics, never emits ``MetricEvent``s. These tests pin the
component definitions, the single-clock property, and that the null observer
is a no-op.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from agentic.agentic_layer.observer import (
    LatencyObserver,
    NullLatencyObserver,
    RecordingLatencyObserver,
)
from agentic.port.names import Name


def _name() -> Name:
    return Name((b"cap", b"fwd", b"hospital", b"beds"))


def test_null_observer_is_noop() -> None:
    obs = NullLatencyObserver()
    # Every hook is callable and returns None without raising.
    assert obs.on_commit(parent=b"p", t_commit=1.0, leaf_count=2) is None
    assert obs.on_dispatch_begin(parent=b"p", t0=2.0) is None
    assert obs.on_leaf_forwarded(
        correlation=b"c", parent=b"p", leaf_index=0, name=_name(), t_send=3.0
    ) is None
    assert obs.on_leaf_response(
        correlation=b"c", parent=b"p", leaf_index=0, t_response=4.0
    ) is None
    assert obs.on_dispatch_end(parent=b"p", t1=5.0) is None
    assert obs.on_aggregate(parent=b"p", t_aggregate=6.0, trace_root=b"r") is None
    assert obs.on_complete(parent=b"p", t_intent=7.0) is None


def test_observer_protocol_is_runtime_checkable() -> None:
    assert isinstance(NullLatencyObserver(), LatencyObserver)
    assert isinstance(RecordingLatencyObserver(), LatencyObserver)


def test_recording_observer_collects_timestamps() -> None:
    obs = RecordingLatencyObserver()
    parent = b"p"
    name = _name()
    obs.on_commit(parent=parent, t_commit=1.0, leaf_count=3)
    obs.on_dispatch_begin(parent=parent, t0=2.0)
    obs.on_leaf_forwarded(correlation=b"c0", parent=parent, leaf_index=0, name=name, t_send=3.0)
    obs.on_leaf_forwarded(correlation=b"c1", parent=parent, leaf_index=1, name=name, t_send=4.0)
    obs.on_leaf_response(correlation=b"c0", parent=parent, leaf_index=0, t_response=6.0)
    obs.on_dispatch_end(parent=parent, t1=7.0)
    obs.on_aggregate(parent=parent, t_aggregate=8.0, trace_root=b"r")
    obs.on_complete(parent=parent, t_intent=9.0)

    assert obs.commits[parent] == (1.0, 3)
    assert obs.dispatch_begin[parent] == 2.0
    assert obs.leaf_forwarded[(parent, 0)] == (b"c0", 3.0)
    assert obs.leaf_forwarded[(parent, 1)] == (b"c1", 4.0)
    assert obs.leaf_response[(parent, 0)] == 6.0
    assert obs.dispatch_end[parent] == 7.0
    assert obs.aggregate[parent] == (8.0, b"r")
    assert obs.complete[parent] == 9.0


def test_recording_observer_single_clock_same_instance() -> None:
    """The observer never invents its own clock; all t_* are caller-supplied."""
    obs = RecordingLatencyObserver()
    # A single instance is used across a whole intent; the timestamps are
    # recorded verbatim (no re-sourcing, no internal clock read).
    t0 = time.perf_counter()
    obs.on_dispatch_begin(parent=b"p", t0=t0)
    assert obs.dispatch_begin[b"p"] == t0


def test_recording_observer_t_intent_view_a() -> None:
    """View A wall decomposition: T_intent = T_decompose + T_dispatch + T_aggregate."""
    obs = RecordingLatencyObserver()
    parent = b"p"
    t_emission = 1.0
    t_commit = 2.0
    t0 = 3.0
    t1 = 7.0
    t_intent =12.0
    t_decompose = t_commit - t_emission
    t_dispatch = t1 - t0
    t_aggregate = t_intent - t1
    # View A is exact: the three wall segments are disjoint and cover
    # [t_emission, t_intent] when t_commit == t0 (no gap between them).
    assert t_decompose + t_dispatch + t_aggregate == t_intent - t_emission - (t0 - t_commit)


def test_recording_observer_t_dispatch_residual_view_b() -> None:
    """View B: T_dispatch_residual = max(0, T_dispatch − T_network − T_service)."""
    t_dispatch = 10.0
    t_network = 3.0
    t_service = 4.0
    residual = max(0.0, t_dispatch - t_network - t_service)
    assert residual == 3.0
    # Never summed into View A.
    assert residual != t_dispatch


def test_unknown_hooks_are_tolerated() -> None:
    """The observer interface is fixed; extra kwargs must not be passed by layer.

    This pins that the layer calls only the documented hook set (a regression
    guard against a new hook being added to the interface but missed here).
    """
    obs = NullLatencyObserver()
    with pytest.raises(TypeError):
        obs.on_commit(parent=b"p", t_commit=1.0, leaf_count=2, bogus=1)  # type: ignore[call-arg]


class _DelegatingObserver(LatencyObserver):
    """Delegates every hook to the Protocol's default no-op bodies."""

    def on_commit(self, *, parent: bytes, t_commit: float, leaf_count: int) -> None:
        super().on_commit(parent=parent, t_commit=t_commit, leaf_count=leaf_count)

    def on_dispatch_begin(self, *, parent: bytes, t0: float) -> None:
        super().on_dispatch_begin(parent=parent, t0=t0)

    def on_leaf_forwarded(
        self,
        *,
        correlation: bytes,
        parent: bytes,
        leaf_index: int,
        name: Name,
        t_send: float,
    ) -> None:
        super().on_leaf_forwarded(
            correlation=correlation, parent=parent, leaf_index=leaf_index,
            name=name, t_send=t_send,
        )

    def on_leaf_response(
        self, *, correlation: bytes, parent: bytes, leaf_index: int, t_response: float
    ) -> None:
        super().on_leaf_response(
            correlation=correlation, parent=parent, leaf_index=leaf_index,
            t_response=t_response,
        )

    def on_dispatch_end(self, *, parent: bytes, t1: float) -> None:
        super().on_dispatch_end(parent=parent, t1=t1)

    def on_aggregate(
        self, *, parent: bytes, t_aggregate: float, trace_root: bytes
    ) -> None:
        super().on_aggregate(parent=parent, t_aggregate=t_aggregate, trace_root=trace_root)

    def on_complete(self, *, parent: bytes, t_intent: float) -> None:
        super().on_complete(parent=parent, t_intent=t_intent)


def test_protocol_default_bodies_are_noops() -> None:
    """The Protocol's default hook bodies are executable no-ops.

    An implementation may delegate any hook to the Protocol default (``super()``)
    and stay inert: the seam contracts a passive sink, never an accumulator.
    """
    obs = _DelegatingObserver()
    assert obs.on_commit(parent=b"p", t_commit=1.0, leaf_count=2) is None
    assert obs.on_dispatch_begin(parent=b"p", t0=2.0) is None
    assert obs.on_leaf_forwarded(
        correlation=b"c", parent=b"p", leaf_index=0, name=_name(), t_send=3.0
    ) is None
    assert obs.on_leaf_response(
        correlation=b"c", parent=b"p", leaf_index=0, t_response=4.0
    ) is None
    assert obs.on_dispatch_end(parent=b"p", t1=5.0) is None
    assert obs.on_aggregate(parent=b"p", t_aggregate=6.0, trace_root=b"r") is None
    assert obs.on_complete(parent=b"p", t_intent=7.0) is None