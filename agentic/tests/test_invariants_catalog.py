# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Named guarding tests for invariants I1–I8 (Track 1 exit criteria)."""

from __future__ import annotations

INVARIANT_GUARDS: dict[str, str] = {
    "I1": "agentic/tests/test_decomposer.py::test_determinism_independent_of_runtime_noise",
    "I2": "agentic/tests/test_context_pit.py::test_workflow_commit_before_forward_ordering",
    "I3": "agentic/tests/test_layer_plugin_mock.py::test_timeout_leaf_becomes_explicit_null",
    "I4": "agentic/tests/test_aggregation.py::test_out_of_order_delivery_same_trace_root",
    "I5": "agentic/tests/test_merkle_rfc6962.py::test_inclusion_proof_verifies_for_every_index",
    "I6": "agentic/tests/test_attestation_i6.py::test_i6_valid_quote_with_false_claim_enters_reputation_unverified",
    "I7": "agentic/tests/test_architecture.py::test_ac4_port_has_no_substrate_types_or_picn_imports",
    "I8": "agentic/tests/test_scenario_e2e.py::test_cardiac_adversary_kpa_deprioritises_not_bans",
}


def test_invariant_catalog_complete() -> None:
    """Every invariant I1–I8 has a named guard entry."""
    assert set(INVARIANT_GUARDS) == {f"I{i}" for i in range(1, 9)}
    for inv, path in INVARIANT_GUARDS.items():
        assert "::" in path, f"{inv} guard must name module::test"
        assert path.startswith("agentic/tests/"), f"{inv} guard must live under agentic/tests"


def test_i1_guard_module_importable() -> None:
    import agentic.tests.test_decomposer as m

    assert hasattr(m, "test_determinism_independent_of_runtime_noise")


def test_i2_guard_module_importable() -> None:
    import agentic.tests.test_context_pit as m

    assert hasattr(m, "test_workflow_commit_before_forward_ordering")


def test_i3_guard_module_importable() -> None:
    import agentic.tests.test_layer_plugin_mock as m

    assert hasattr(m, "test_timeout_leaf_becomes_explicit_null")


def test_i4_guard_module_importable() -> None:
    import agentic.tests.test_aggregation as m

    assert hasattr(m, "test_out_of_order_delivery_same_trace_root")


def test_i5_guard_exists() -> None:
    import agentic.tests.test_merkle_rfc6962 as m

    assert hasattr(m, "test_inclusion_proof_verifies_for_every_index")


def test_i6_guard_exists() -> None:
    import agentic.tests.test_attestation_i6 as m

    assert hasattr(m, "test_i6_valid_quote_with_false_claim_enters_reputation_unverified")


def test_i7_port_independence_guard_exists() -> None:
    import agentic.tests.test_architecture as m

    assert hasattr(m, "test_ac4_port_has_no_substrate_types_or_picn_imports")


def test_i8_deprioritise_guard_exists() -> None:
    import agentic.tests.test_scenario_e2e as m

    assert hasattr(m, "test_cardiac_adversary_kpa_deprioritises_not_bans")
