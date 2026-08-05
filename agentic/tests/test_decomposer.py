# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests for bounded decomposition determinism and refusal paths."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agentic.agentic_layer.decomposer import (
    BoundedDecomposer,
    expected_set_from,
    load_template,
)
from agentic.port.errors import BoundExceeded, NoTemplateMatched
from agentic.trust import generate_ed25519_private_key, sign_artefact


def _envelope(
    *,
    template_id: str,
    root: dict[str, Any],
    fanout: int,
    depth: int,
    predicates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = generate_ed25519_private_key()
    body = {
        "kind": "task-graph-template",
        "template_id": template_id,
        "version": "1.0.0",
        "declared_fanout": fanout,
        "declared_depth": depth,
        "predicates": predicates or {"scenario": "cardiac"},
        "root": root,
    }
    return sign_artefact(body, key)


def test_decompose_par_emits_all_leaves() -> None:
    root = {
        "op": "PAR",
        "children": [
            {"op": "LEAF", "capability": ["eta"]},
            {"op": "LEAF", "capability": ["rank"]},
            {"op": "LEAF", "capability": ["hospital", "beds"]},
        ],
    }
    tmpl = load_template(_envelope(template_id="t1", root=root, fanout=4, depth=2), f_max=8, d_max=4)
    dec = BoundedDecomposer(templates=[tmpl], f_max=8, d_max=4)
    selected, leaves = dec.decompose(
        intent_attrs={"scenario": "cardiac"},
        bindings={"patient": "p1"},
    )
    assert selected.template_id == "t1"
    assert [leaf.capability for leaf in leaves] == [
        ("eta",),
        ("rank",),
        ("hospital", "beds"),
    ]
    assert all(leaf.bindings == {"patient": "p1"} for leaf in leaves)


def test_no_template_match_refuses() -> None:
    root = {"op": "LEAF", "capability": ["x"]}
    tmpl = load_template(
        _envelope(template_id="t1", root=root, fanout=1, depth=1, predicates={"scenario": "other"}),
        f_max=8,
        d_max=4,
    )
    dec = BoundedDecomposer(templates=[tmpl], f_max=8, d_max=4)
    with pytest.raises(NoTemplateMatched):
        dec.decompose(intent_attrs={"scenario": "cardiac"}, bindings={})


def test_over_bound_template_refused_at_load() -> None:
    root = {
        "op": "PAR",
        "children": [
            {"op": "LEAF", "capability": ["a"]},
            {"op": "LEAF", "capability": ["b"]},
            {"op": "LEAF", "capability": ["c"]},
        ],
    }
    with pytest.raises(BoundExceeded, match="exceed configured"):
        load_template(
            _envelope(template_id="t1", root=root, fanout=8, depth=2),
            f_max=2,
            d_max=4,
        )


def test_seq_and_alt_expand_to_full_leaf_set() -> None:
    root = {
        "op": "SEQ",
        "children": [
            {"op": "ALT", "children": [
                {"op": "LEAF", "capability": ["a"]},
                {"op": "LEAF", "capability": ["b"]},
            ]},
            {"op": "LEAF", "capability": ["c"]},
        ],
    }
    tmpl = load_template(_envelope(template_id="t2", root=root, fanout=4, depth=3), f_max=8, d_max=4)
    _, leaves = BoundedDecomposer([tmpl], 8, 4).decompose(
        intent_attrs={"scenario": "cardiac"}, bindings={}
    )
    assert [leaf.capability for leaf in leaves] == [("a",), ("b",), ("c",)]


def test_load_rejects_bad_nodes_and_declared_bounds() -> None:
    with pytest.raises(BoundExceeded, match="unsupported operator"):
        load_template(
            _envelope(
                template_id="t",
                root={"op": "XOR", "children": [{"op": "LEAF", "capability": ["x"]}]},
                fanout=2,
                depth=2,
            ),
            f_max=8,
            d_max=4,
        )
    with pytest.raises(BoundExceeded, match="LEAF requires"):
        load_template(
            _envelope(template_id="t", root={"op": "LEAF", "capability": []}, fanout=1, depth=1),
            f_max=8,
            d_max=4,
        )
    with pytest.raises(BoundExceeded, match="declared bounds"):
        load_template(
            _envelope(
                template_id="t",
                root={"op": "LEAF", "capability": ["x"]},
                fanout=100,
                depth=1,
            ),
            f_max=8,
            d_max=4,
        )
    with pytest.raises(BoundExceeded, match="children"):
        load_template(
            _envelope(template_id="t", root={"op": "PAR", "children": []}, fanout=1, depth=1),
            f_max=8,
            d_max=4,
        )
    with pytest.raises(BoundExceeded, match="f_max"):
        load_template(
            _envelope(template_id="t", root={"op": "LEAF", "capability": ["x"]}, fanout=1, depth=1),
            f_max=0,
            d_max=1,
        )
    key = generate_ed25519_private_key()
    bad = sign_artefact(
        {
            "kind": "capability-descriptor",
            "domain": "d",
            "task": "t",
            "version": "1",
            "constraints": {},
            "attestation_policy": "none",
            "reputation_threshold": "0",
            "cost": "1",
            "input_schema": {},
            "output_schema": {},
            "freshness_bound_s": 1,
            "revocation_pointer": "/r",
        },
        key,
    )
    with pytest.raises(BoundExceeded, match="task-graph-template"):
        load_template(bad, f_max=8, d_max=4)


@given(
    width=st.integers(min_value=1, max_value=5),
    binding_val=st.text(min_size=0, max_size=8),
)
@settings(max_examples=40, deadline=None)
def test_determinism_independent_of_runtime_noise(width: int, binding_val: str) -> None:
    children = [{"op": "LEAF", "capability": [f"c{i}"]} for i in range(width)]
    root = {"op": "PAR", "children": children} if width > 1 else children[0]
    tmpl = load_template(
        _envelope(template_id="det", root=root, fanout=max(width, 1), depth=2),
        f_max=16,
        d_max=8,
    )
    bindings = {"k": binding_val}
    dec = BoundedDecomposer(templates=[tmpl], f_max=16, d_max=8)
    _, a = dec.decompose(intent_attrs={"scenario": "cardiac"}, bindings=bindings)
    _, b = dec.decompose(intent_attrs={"scenario": "cardiac"}, bindings=bindings)
    assert a == b
    assert a == expected_set_from(tmpl, bindings)
    # Runtime noise must not change the set
    noisy = BoundedDecomposer(templates=[tmpl], f_max=16, d_max=8)
    _, c = noisy.decompose(
        intent_attrs={"scenario": "cardiac", "load": "high", "cfib": "ignored"},
        bindings=bindings,
    )
    assert c == a
