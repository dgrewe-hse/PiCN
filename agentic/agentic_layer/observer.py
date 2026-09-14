# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""``LatencyObserver`` — passive per-leaf timing hooks for the T_intent split.

The observer is an injectable passive interface on :class:`~agentic.agentic_layer.layer.AgenticLayer`.
The layer calls hooks only; it never computes derived metrics and never emits
``MetricEvent``\\ s (that stays in ``agentic.benchmark``). The observer never
reads its own clock — every ``t_*`` timestamp is caller-supplied, so a single
clock per tier (intake monotonic, port clock, producer clock) is preserved and
there is no double-counting (design v4 §4.2).

Three roles are served:

* :class:`LatencyObserver` — the ``Protocol`` defining the hook set.
* :class:`NullLatencyObserver` — a no-op default (no measurement overhead).
* :class:`RecordingLatencyObserver` — collects timestamps for the runner.

No ``PiCN.*`` and no LLM imports here (AC1/AC3): this is a pure timing sink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from agentic.port.names import Name


@runtime_checkable
class LatencyObserver(Protocol):
    """Hook set for the T_intent decomposition (design v4 §4.2).

    Timestamps are in the caller's clock domain; the observer records them
    verbatim and never re-sources them.
    """

    def on_commit(self, *, parent: bytes, t_commit: float, leaf_count: int) -> None:
        """Called immediately after the Context PIT commit (``t0``)."""
        ...

    def on_dispatch_begin(self, *, parent: bytes, t0: float) -> None:
        """Called when the dispatch block begins (after the prepass)."""
        ...

    def on_leaf_forwarded(
        self,
        *,
        correlation: bytes,
        parent: bytes,
        leaf_index: int,
        name: Name,
        t_send: float,
    ) -> None:
        """Called when a leaf's ``RequestSent`` is observed (``t_send``)."""
        ...

    def on_leaf_response(
        self,
        *,
        correlation: bytes,
        parent: bytes,
        leaf_index: int,
        t_response: float,
    ) -> None:
        """Called when a leaf's ``ResponseArrived`` is observed (``t_response``)."""
        ...

    def on_dispatch_end(self, *, parent: bytes, t1: float) -> None:
        """Called when the dispatch block ends (``t1``)."""
        ...

    def on_aggregate(
        self, *, parent: bytes, t_aggregate: float, trace_root: bytes
    ) -> None:
        """Called when aggregation completes (``t_agg``)."""
        ...

    def on_complete(self, *, parent: bytes, t_intent: float) -> None:
        """Called when the intent's ``done`` resolves (``t_intent``)."""
        ...


class NullLatencyObserver:
    """A no-op :class:`LatencyObserver` — the default (zero measurement cost)."""

    def on_commit(self, *, parent: bytes, t_commit: float, leaf_count: int) -> None:
        return None

    def on_dispatch_begin(self, *, parent: bytes, t0: float) -> None:
        return None

    def on_leaf_forwarded(
        self,
        *,
        correlation: bytes,
        parent: bytes,
        leaf_index: int,
        name: Name,
        t_send: float,
    ) -> None:
        return None

    def on_leaf_response(
        self,
        *,
        correlation: bytes,
        parent: bytes,
        leaf_index: int,
        t_response: float,
    ) -> None:
        return None

    def on_dispatch_end(self, *, parent: bytes, t1: float) -> None:
        return None

    def on_aggregate(
        self, *, parent: bytes, t_aggregate: float, trace_root: bytes
    ) -> None:
        return None

    def on_complete(self, *, parent: bytes, t_intent: float) -> None:
        return None


@dataclass
class RecordingLatencyObserver:
    """A :class:`LatencyObserver` that collects timestamps per intent.

    Stored verbatim, keyed by ``parent`` intent digest (or ``(parent, leaf_index)``
    for leaf-scoped hooks). Every timestamp is caller-supplied — the observer
    never invents a clock (single-clock per tier, anti-double-count).
    """

    commits: dict[bytes, tuple[float, int]] = field(default_factory=dict)
    dispatch_begin: dict[bytes, float] = field(default_factory=dict)
    leaf_forwarded: dict[tuple[bytes, int], tuple[bytes, float]] = field(
        default_factory=dict
    )
    leaf_response: dict[tuple[bytes, int], float] = field(default_factory=dict)
    dispatch_end: dict[bytes, float] = field(default_factory=dict)
    aggregate: dict[bytes, tuple[float, bytes]] = field(default_factory=dict)
    complete: dict[bytes, float] = field(default_factory=dict)

    def on_commit(self, *, parent: bytes, t_commit: float, leaf_count: int) -> None:
        self.commits[parent] = (t_commit, leaf_count)

    def on_dispatch_begin(self, *, parent: bytes, t0: float) -> None:
        self.dispatch_begin[parent] = t0

    def on_leaf_forwarded(
        self,
        *,
        correlation: bytes,
        parent: bytes,
        leaf_index: int,
        name: Name,
        t_send: float,
    ) -> None:
        self.leaf_forwarded[(parent, leaf_index)] = (correlation, t_send)

    def on_leaf_response(
        self,
        *,
        correlation: bytes,
        parent: bytes,
        leaf_index: int,
        t_response: float,
    ) -> None:
        self.leaf_response[(parent, leaf_index)] = t_response

    def on_dispatch_end(self, *, parent: bytes, t1: float) -> None:
        self.dispatch_end[parent] = t1

    def on_aggregate(
        self, *, parent: bytes, t_aggregate: float, trace_root: bytes
    ) -> None:
        self.aggregate[parent] = (t_aggregate, trace_root)

    def on_complete(self, *, parent: bytes, t_intent: float) -> None:
        self.complete[parent] = t_intent


__all__ = ["LatencyObserver", "NullLatencyObserver", "RecordingLatencyObserver"]