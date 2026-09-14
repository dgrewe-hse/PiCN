# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""``concurrent_publishable`` gate: only a genuinely paired, same-leaf-set,
non-zero-latency cell with an intact T_intent decomposition is publishable.

The gate is adversarial by design (design v4 §4.5): it refuses unpaired runs,
zero-latency-as-speedup, differing trace roots, a ``control``/``leaf_latency_s``
mismatch, and a zero/unmeasured ``T_service`` presented as a five-way
attribution.
"""

from __future__ import annotations

import math

import pytest

from agentic.benchmark.concurrency import (
    concurrent_publishable,
    fanout_speedup,
    build_concurrency_cell,
)


# --- helpers ----------------------------------------------------------------


def _record(
    *,
    transport: str = "bus",
    seed: int = 1,
    k: int = 2,
    leaf_latency_s: float = 0.05,
    control: bool | None = None,
    expected_digest: str = "deadbeef",
    trace_root_equal: bool = True,
    serial_ms: float = 20.0,
    concurrent_ms: float = 5.0,
    t_service_ms: float | None = 4.0,
    t_network_ms: float | None = 0.5,
    measured: bool = True,
    view: str = "A",
    t_dispatch_residual_ms: float | None = None,
) -> dict:
    if control is None:
        control = leaf_latency_s == 0.0
    if t_service_ms is not None and t_service_ms == 0.0 and not measured:
        pass
    record = {
        "kind": "concurrency_run",
        "transport": transport,
        "seed": seed,
        "k": k,
        "leaf_latency_s": leaf_latency_s,
        "control": control,
        "expected_subintent_set_serial": expected_digest,
        "expected_subintent_set_concurrent": expected_digest,
        "trace_root_equal": trace_root_equal,
        "fanout_serial_ms": serial_ms,
        "fanout_concurrent_ms": concurrent_ms,
        "fanout_speedup": serial_ms / concurrent_ms if concurrent_ms else None,
        "T_intent_view": view,
        "t_intent_ms": serial_ms,
        "t_dispatch_ms": serial_ms - t_service_ms if t_service_ms else 1.0,
    }
    if t_service_ms is not None:
        record["t_service_ms"] = t_service_ms
        record["t_service_measured"] = measured
    if t_network_ms is not None:
        record["t_network_ms"] = t_network_ms
    if t_dispatch_residual_ms is not None:
        record["t_dispatch_residual_ms"] = t_dispatch_residual_ms
    return record


# --- gate: happy path -------------------------------------------------------


def test_publishable_true_on_valid_paired_cell() -> None:
    rec = _record()
    assert concurrent_publishable(rec) is True


def test_publishable_true_with_control_false_leaf_latency_positive() -> None:
    rec = _record(leaf_latency_s=0.1, control=False)
    assert concurrent_publishable(rec) is True


def test_speedup_computation() -> None:
    assert fanout_speedup(serial_ms=20.0, concurrent_ms=5.0) == pytest.approx(4.0)
    assert fanout_speedup(serial_ms=20.0, concurrent_ms=0.0) is None


def test_build_cell_requires_both_modes() -> None:
    serial = {"serial_ms": 10.0, "concurrent_ms": None}
    with pytest.raises(ValueError):
        build_concurrency_cell(serial=serial, concurrent={})  # type: ignore[arg-type]


def test_build_cell_ok() -> None:
    serial = {"serial_ms": 10.0}
    concurrent = {"concurrent_ms": 5.0}
    cell = build_concurrency_cell(serial=serial, concurrent=concurrent)
    assert cell["fanout_speedup"] == pytest.approx(2.0)


# --- gate: adversarial cases ------------------------------------------------


def test_refuse_unpaired_missing_concurrent() -> None:
    rec = _record()
    rec.pop("fanout_concurrent_ms")
    rec.pop("fanout_speedup")
    assert concurrent_publishable(rec) is False


def test_refuse_unpaired_missing_serial() -> None:
    rec = _record()
    rec.pop("fanout_serial_ms")
    assert concurrent_publishable(rec) is False


def test_refuse_zero_latency_as_speedup() -> None:
    """A zero-latency control cell can never be a publishable speedup claim."""
    rec = _record(leaf_latency_s=0.0, control=True, serial_ms=5.0, concurrent_ms=5.0)
    assert concurrent_publishable(rec) is False


def test_refuse_control_leaf_latency_mismatch() -> None:
    """control=true with leaf_latency_s>0 is malformed; gate rejects it."""
    rec = _record(leaf_latency_s=0.05, control=True)
    assert concurrent_publishable(rec) is False


def test_refuse_control_false_with_zero_latency() -> None:
    rec = _record(leaf_latency_s=0.0, control=False)
    assert concurrent_publishable(rec) is False


def test_refuse_differing_trace_root() -> None:
    rec = _record(trace_root_equal=False)
    assert concurrent_publishable(rec) is False


def test_refuse_non_finite_speedup() -> None:
    rec = _record()
    rec["fanout_speedup"] = math.inf
    assert concurrent_publishable(rec) is False


def test_refuse_non_positive_speedup() -> None:
    rec = _record(serial_ms=0.0, concurrent_ms=10.0)
    rec["fanout_speedup"] = 0.0
    assert concurrent_publishable(rec) is False


def test_refuse_differing_leaf_set() -> None:
    """Different expected_subintent_set digest → not the same leaf set."""
    rec = _record(expected_digest="deadbeef")
    rec["expected_subintent_set_concurrent"] = "cafebabe"
    assert concurrent_publishable(rec) is False


def test_refuse_zero_t_service_five_way() -> None:
    """Five-way decomposition with zero T_service is not publishable."""
    rec = _record(t_service_ms=0.0, measured=True)
    assert concurrent_publishable(rec) is False


def test_refuse_unmeasured_t_service_five_way() -> None:
    rec = _record(t_service_ms=0.0, measured=False)
    assert concurrent_publishable(rec) is False


def test_allow_four_way_when_t_service_not_measurable() -> None:
    """Four-way with T_service explicitly not_measurable and View B residual."""
    rec = _record(t_service_ms=None, t_network_ms=0.5, view="B", t_dispatch_residual_ms=2.0)
    # View B with explicit T_dispatch_residual; no five-way claim.
    assert concurrent_publishable(rec) is True


def test_refuse_view_b_without_residual() -> None:
    """View B requires an explicit T_dispatch_residual to be publishable."""
    rec = _record(t_service_ms=None, t_network_ms=0.5, view="B")
    assert concurrent_publishable(rec) is False


def test_refuse_wrong_transport() -> None:
    rec = _record(transport="udp")
    assert concurrent_publishable(rec) is False


def test_requires_t_intent_components() -> None:
    rec = _record()
    rec.pop("t_service_ms")
    rec.pop("t_network_ms")
    assert concurrent_publishable(rec) is False


def test_refuse_missing_fanout_speedup_only() -> None:
    """Both fan-out times present but the speedup itself is absent."""
    rec = _record()
    rec.pop("fanout_speedup")
    assert concurrent_publishable(rec) is False


def test_refuse_none_speedup_reaches_finite_positive() -> None:
    """A None fanout_speedup hits the finite/positive guard, not the presence check."""
    rec = _record()
    rec["fanout_speedup"] = None
    assert concurrent_publishable(rec) is False


def test_refuse_view_a_with_residual() -> None:
    """View A (wall-time view) must not present a T_dispatch_residual."""
    rec = _record(view="A", t_dispatch_residual_ms=1.0)
    assert concurrent_publishable(rec) is False


def test_refuse_view_b_with_zero_t_service_five_way() -> None:
    """View B presenting a five-way attribution with zero T_service is refused."""
    rec = _record(view="B", t_dispatch_residual_ms=2.0, t_service_ms=0.0, measured=True)
    assert concurrent_publishable(rec) is False


def test_refuse_view_b_with_unmeasured_t_service_five_way() -> None:
    rec = _record(view="B", t_dispatch_residual_ms=2.0, t_service_ms=0.0, measured=False)
    assert concurrent_publishable(rec) is False


def test_refuse_missing_both_intent_and_dispatch_ms() -> None:
    """The gate requires at least one of t_intent_ms / t_dispatch_ms."""
    rec = _record()
    rec.pop("t_intent_ms")
    rec.pop("t_dispatch_ms")
    assert concurrent_publishable(rec) is False