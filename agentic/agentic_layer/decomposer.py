# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Bounded decomposer and signed task-graph templates.

The emitted sub-intent **set** is a pure function of
``(task_template_id, deterministic_bindings)``. Runtime state (C-FIB contents,
reputation, load) must not affect whether a sub-intent exists — only where it
is forwarded later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from agentic.port.errors import BoundExceeded, NoTemplateMatched
from agentic.trust.signed_artefact import (
    ArtefactVerificationError,
    SignedArtefact,
    verify_artefact,
)

Operator = Literal["SEQ", "PAR", "ALT", "LEAF"]


@dataclass(frozen=True, slots=True)
class TemplateNode:
    """One node in a task-graph template.

    :param op: Operator or ``LEAF``.
    :param capability: Capability path components for a leaf (UTF-8 strings).
    :param children: Child nodes for ``SEQ`` / ``PAR`` / ``ALT``.
    """

    op: Operator
    capability: tuple[str, ...] = ()
    children: tuple[TemplateNode, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskGraphTemplate:
    """Loaded, bound-checked task-graph template.

    :param template_id: Stable identifier used as the determinism input.
    :param version: Template version string.
    :param predicates: Match predicates for template selection.
    :param root: Root of the operator tree.
    :param declared_fanout: Max fan-out declared by the template author.
    :param declared_depth: Max depth declared by the template author.
    :param artefact: Verified signed artefact.
    """

    template_id: str
    version: str
    predicates: Mapping[str, Any]
    root: TemplateNode
    declared_fanout: int
    declared_depth: int
    artefact: SignedArtefact


@dataclass(frozen=True, slots=True)
class SubIntent:
    """One emitted sub-intent (expected-set member).

    :param index: Canonical index in emission order.
    :param capability: Capability path for this leaf.
    :param bindings: Deterministic bindings copied into the leaf.
    """

    index: int
    capability: tuple[str, ...]
    bindings: Mapping[str, Any]


def _parse_node(raw: Mapping[str, Any]) -> TemplateNode:
    op = raw.get("op")
    if op not in ("SEQ", "PAR", "ALT", "LEAF"):
        raise BoundExceeded(detail=f"unsupported operator: {op!r}")
    if op == "LEAF":
        cap = raw.get("capability")
        if not isinstance(cap, list) or not cap or not all(isinstance(x, str) for x in cap):
            raise BoundExceeded(detail="LEAF requires capability: list[str]")
        return TemplateNode(op="LEAF", capability=tuple(cap))
    children_raw = raw.get("children")
    if not isinstance(children_raw, list) or not children_raw:
        raise BoundExceeded(detail=f"{op} requires non-empty children")
    children = tuple(_parse_node(c) for c in children_raw if isinstance(c, dict))
    if len(children) != len(children_raw):
        raise BoundExceeded(detail="child nodes must be objects")
    return TemplateNode(op=op, children=children)  # op narrowed above


def _measure_fanout(node: TemplateNode) -> int:
    if node.op == "LEAF":
        return 1
    if node.op in ("PAR", "ALT"):
        return max(len(node.children), max((_measure_fanout(c) for c in node.children), default=1))
    # SEQ: fan-out is max over children
    return max((_measure_fanout(c) for c in node.children), default=1)


def _measure_depth(node: TemplateNode) -> int:
    if node.op == "LEAF":
        return 1
    return 1 + max((_measure_depth(c) for c in node.children), default=0)


def load_template(
    envelope: Mapping[str, Any],
    *,
    f_max: int,
    d_max: int,
) -> TaskGraphTemplate:
    """Verify and load a task-graph template, enforcing configured bounds.

    :param envelope: Signed artefact envelope (``kind=task-graph-template``).
    :param f_max: Configured maximum fan-out (swept by the harness).
    :param d_max: Configured maximum depth (swept by the harness).
    :return: Bound-checked template.
    :raises BoundExceeded: If declared or measured bounds exceed limits, or
        the artefact is invalid for use as a template.
    """
    if f_max < 1 or d_max < 1:
        raise BoundExceeded(detail="f_max and d_max must be >= 1")
    try:
        artefact = verify_artefact(envelope)
    except ArtefactVerificationError as exc:
        raise BoundExceeded(detail=f"template verification failed: {exc}") from exc
    if artefact.kind != "task-graph-template":
        raise BoundExceeded(detail=f"expected task-graph-template, got {artefact.kind!r}")
    body = artefact.body
    template_id = body.get("template_id")
    version = body.get("version")
    if not isinstance(template_id, str) or not template_id:
        raise BoundExceeded(detail="template_id required")
    if not isinstance(version, str) or not version:
        raise BoundExceeded(detail="version required")
    declared_fanout = body.get("declared_fanout")
    declared_depth = body.get("declared_depth")
    if type(declared_fanout) is not int or declared_fanout < 1:
        raise BoundExceeded(detail="declared_fanout must be a positive int")
    if type(declared_depth) is not int or declared_depth < 1:
        raise BoundExceeded(detail="declared_depth must be a positive int")
    if declared_fanout > f_max or declared_depth > d_max:
        raise BoundExceeded(
            detail=(
                f"template declared bounds fanout={declared_fanout} depth={declared_depth} "
                f"exceed configured f_max={f_max} d_max={d_max}"
            )
        )
    root_raw = body.get("root")
    if not isinstance(root_raw, dict):
        raise BoundExceeded(detail="root must be an object")
    root = _parse_node(root_raw)
    measured_f = _measure_fanout(root)
    measured_d = _measure_depth(root)
    if measured_f > f_max or measured_d > d_max:
        raise BoundExceeded(
            detail=(
                f"measured bounds fanout={measured_f} depth={measured_d} "
                f"exceed configured f_max={f_max} d_max={d_max}"
            )
        )
    predicates = body.get("predicates")
    if not isinstance(predicates, dict):
        predicates = {}
    return TaskGraphTemplate(
        template_id=template_id,
        version=version,
        predicates=predicates,
        root=root,
        declared_fanout=declared_fanout,
        declared_depth=declared_depth,
        artefact=artefact,
    )


def _emit(node: TemplateNode, bindings: Mapping[str, Any], out: list[SubIntent]) -> None:
    if node.op == "LEAF":
        out.append(
            SubIntent(index=len(out), capability=node.capability, bindings=dict(bindings))
        )
        return
    # SEQ / PAR / ALT all expand to the full child leaf set for the expected
    # set. Operator choice affects execution scheduling later, not membership.
    for child in node.children:
        _emit(child, bindings, out)


def _predicates_match(predicates: Mapping[str, Any], intent_attrs: Mapping[str, Any]) -> bool:
    for key, expected in predicates.items():
        if intent_attrs.get(key) != expected:
            return False
    return True


@dataclass
class BoundedDecomposer:
    """Selects a template and emits a deterministic sub-intent set.

    :param templates: Loaded templates available for matching.
    :param f_max: Configured fan-out bound (informational; enforced at load).
    :param d_max: Configured depth bound (informational; enforced at load).
    """

    templates: Sequence[TaskGraphTemplate]
    f_max: int
    d_max: int

    def decompose(
        self,
        *,
        intent_attrs: Mapping[str, Any],
        bindings: Mapping[str, Any],
    ) -> tuple[TaskGraphTemplate, tuple[SubIntent, ...]]:
        """Decompose an intent into a deterministic sub-intent set.

        :param intent_attrs: Attributes matched against template predicates.
        :param bindings: Deterministic bindings folded into each leaf.
        :return: ``(selected_template, sub_intents)``.
        :raises NoTemplateMatched: If no template predicates match.
        """
        for template in self.templates:
            if _predicates_match(template.predicates, intent_attrs):
                leaves: list[SubIntent] = []
                _emit(template.root, bindings, leaves)
                return template, tuple(leaves)
        raise NoTemplateMatched(detail="no template predicates matched")


def expected_set_from(
    template: TaskGraphTemplate,
    bindings: Mapping[str, Any],
) -> tuple[SubIntent, ...]:
    """Independently recompute the expected set (verifier / property tests)."""
    leaves: list[SubIntent] = []
    _emit(template.root, bindings, leaves)
    return tuple(leaves)
