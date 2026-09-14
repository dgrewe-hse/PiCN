# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Experiment D: concurrency snapshot, speedup, and the publishability gate.

This module is pure Python — it never imports ``PiCN.*`` (AC1) and never imports
an LLM (AC3). It derives the ``concurrent_publishable`` gate and the
speedup-vs-``k`` / T_intent decomposition figures from paired
serial/concurrent JSONL records.

The gate is adversarial by construction (design v4 §4.5): a speedup claim is
only publishable from a genuinely **paired** same-leaf-set, non-zero-latency
cell whose trace roots are byte-identical and whose T_intent decomposition is
intact. Zero-latency cells are controls bound to ``control=true`` and can never
satisfy a speedup claim.
"""

from __future__ import annotations

import math
from typing import Any

# The transport on which a concurrency speedup claim is publishable. The gate
# refuses any other transport so a physical (UDP) run can never be mistaken for
# a bus-mechanism illustration (design v4 §4.5 condition 1, §5.5).
PUBLISHABLE_TRANSPORT = "bus"

# The exact record kind this gate / figure layer consumes (design v4 §4.5,
# namespace isolation enforced by the AST check on the generator).
CONCURRENCY_RUN_KIND = "concurrency_run"


def fanout_speedup(*, serial_ms: float, concurrent_ms: float) -> float | None:
    """Return ``serial / concurrent``, or ``None`` when the denominator is 0.

    :param serial_ms: Serial fan-out wall-clock (ms).
    :param concurrent_ms: Concurrent fan-out wall-clock (ms).
    :return: Speedup ratio, or ``None`` when ``concurrent_ms <= 0`` (undefined).
    """
    if concurrent_ms <= 0:
        return None
    return serial_ms / concurrent_ms


def build_concurrency_cell(*, serial: dict[str, Any], concurrent: dict[str, Any]) -> dict[str, Any]:
    """Combine a paired serial/concurrent measurement into one cell record.

    :param serial: Serial-mode measurement dict (must contain ``serial_ms``).
    :param concurrent: Concurrent-mode measurement dict (must contain ``concurrent_ms``).
    :return: A cell with ``fanout_serial_ms``, ``fanout_concurrent_ms``, and\n        ``fanout_speedup``.
    :raises ValueError: If either mode is missing its timing.
    """
    if "serial_ms" not in serial or "concurrent_ms" not in concurrent:
        raise ValueError("a concurrency cell requires both serial_ms and concurrent_ms")
    serial_ms = float(serial["serial_ms"])
    concurrent_ms = float(concurrent["concurrent_ms"])
    return {
        "fanout_serial_ms": serial_ms,
        "fanout_concurrent_ms": concurrent_ms,
        "fanout_speedup": fanout_speedup(serial_ms=serial_ms, concurrent_ms=concurrent_ms),
    }


def _finite_positive(value: float | None) -> bool:
    """True when ``value`` is finite and strictly positive."""
    if value is None:
        return False
    return math.isfinite(value) and value > 0


def concurrent_publishable(record: dict[str, Any]) -> bool:
    """Return True iff ``record`` satisfies the ``concurrent_publishable`` gate.

    The gate implements design v4 §4.5 conditions 1–8. It refuses any record
    that is unpaired, a zero-latency control presented as a speedup, a
    ``control``/``leaf_latency_s`` mismatch, a differing trace root, a differing
    leaf set, a non-finite/non-positive speedup, a zero/unmeasured ``T_service``
    presented as a five-way attribution, or a View-B record without an explicit
    ``T_dispatch_residual``.

    :param record: A ``concurrency_run`` cell record.
    :return: ``True`` only for a genuinely publishable paired cell.
    """
    # Condition 1: both fan-out times exist for the same cell.
    if "fanout_serial_ms" not in record or "fanout_concurrent_ms" not in record:
        return False
    if "fanout_speedup" not in record:
        return False

    # Condition 1 (transport): the concurrency claim is bus-scoped.
    if record.get("transport") != PUBLISHABLE_TRANSPORT:
        return False

    # Condition 3: leaf_latency_s must be strictly positive (a speedup claim is
    # meaningless at zero latency) — enforced structurally here.
    leaf_latency_s = record.get("leaf_latency_s")
    if leaf_latency_s is None or not (isinstance(leaf_latency_s, (int, float)) and leaf_latency_s > 0):
        return False

    # Condition 2: control must be structurally bound to leaf_latency_s.
    # Because condition 3 already required leaf_latency_s > 0 above, the bound
    # control == (leaf_latency_s == 0.0) forces control to be False here — a
    # zero-latency control (control=true) can never reach a speedup claim.
    control = record.get("control")
    if control != (leaf_latency_s == 0.0):
        # A leaf_latency_s>0 record with control=true is malformed; likewise a
        # leaf_latency_s=0 record with control=false.
        return False

    # Condition 6: speedup finite and > 0.
    speedup = record.get("fanout_speedup")
    if not _finite_positive(float(speedup) if speedup is not None else None):
        return False

    # Condition 5: trace roots must be byte-identical.
    if record.get("trace_root_equal") is not True:
        return False

    # Condition 4: same leaf set — identical expected_subintent_set digests in
    # both modes (the anti-inflation guard: never divide independent means).
    expected_serial = record.get("expected_subintent_set_serial")
    expected_concurrent = record.get("expected_subintent_set_concurrent")
    if not expected_serial or not expected_concurrent:
        return False
    if expected_serial != expected_concurrent:
        return False

    # Conditions 7/8: T_intent decomposition. View A needs T_service present and
    # measured (non-zero). View B needs an explicit T_dispatch_residual and a
    # four-way (not five-way) attribution.
    view = record.get("T_intent_view")
    t_service_ms = record.get("t_service_ms")
    t_service_measured = record.get("t_service_measured", True)
    if view == "A":
        if t_service_ms is None or not t_service_measured or float(t_service_ms) <= 0:
            return False
        # View A must NOT present a residual (it is the wall-time view).
        if record.get("t_dispatch_residual_ms") is not None:
            return False
    elif view == "B":
        if record.get("t_dispatch_residual_ms") is None:
            return False
        # A five-way attribution with a zero/unmeasured T_service is refused.
        if t_service_ms is not None and (not t_service_measured or float(t_service_ms) <= 0):
            return False
    else:
        return False

    # The gate requires the T_intent components to be present for the claim.
    if "t_intent_ms" not in record and "t_dispatch_ms" not in record:
        return False

    return True


__all__ = [
    "CONCURRENCY_RUN_KIND",
    "PUBLISHABLE_TRANSPORT",
    "build_concurrency_cell",
    "concurrent_publishable",
    "fanout_speedup",
]