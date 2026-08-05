# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Capability FIB: two-stage match (LPM then predicates).

Stage 1 matches on ``/cap/<issuer>/<capability-path...>`` excluding the version
component. Stage 2 filters candidates by jurisdiction, attestation policy,
latency, cost, semver, and a reputation ``score()`` seam (model arrives later).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Protocol

from agentic.agentic_layer.descriptor import CapabilityDescriptor
from agentic.agentic_layer.naming import capability_lpm_prefix
from agentic.port.names import Name


class ReputationScorer(Protocol):
    """Seam for the Phase E reputation model.

    :param identity: Opaque agent/endpoint identity (typically key digest).
    :param now: Injected clock reading.
    :return: Score in ``[0, 1]``.
    """

    def score(self, identity: bytes, *, now: float) -> float: ...


class OptimisticReputation:
    """Default scorer: every identity scores ``1.0`` (no de-prioritisation yet)."""

    def score(self, identity: bytes, *, now: float) -> float:
        return 1.0


@dataclass(frozen=True, slots=True)
class MatchConstraints:
    """Caller predicates applied in stage 2.

    :param jurisdiction: Required jurisdiction if set.
    :param max_latency_ms: Reject descriptors whose latency bound exceeds this.
    :param max_cost: Maximum acceptable cost (decimal string compare).
    :param min_version: Minimum acceptable semver (inclusive).
    :param require_attestation: If True, keep only ``attestation_policy==\"required\"``.
    """

    jurisdiction: str | None = None
    max_latency_ms: int | None = None
    max_cost: str | None = None
    min_version: str | None = None
    require_attestation: bool = False


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a dotted ``major.minor.patch`` version (extra labels ignored).

    :param version: Semver-like string.
    :return: ``(major, minor, patch)``.
    :raises ValueError: If the core triple cannot be parsed.
    """
    core = version.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if len(parts) < 1 or len(parts) > 3:
        raise ValueError(f"invalid semver: {version!r}")
    nums = [int(p) for p in parts]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _cost_le(left: str, right: str) -> bool:
    try:
        return Decimal(left) <= Decimal(right)
    except InvalidOperation:
        return False


@dataclass
class CapabilityFIB:
    """In-memory C-FIB keyed by LPM prefixes (version excluded).

    :param reputation: Scorer consulted in stage 2 (injectable).
    """

    reputation: ReputationScorer = field(default_factory=OptimisticReputation)
    _by_prefix: dict[Name, list[CapabilityDescriptor]] = field(default_factory=dict)

    def register(self, descriptor: CapabilityDescriptor) -> None:
        """Add a verified descriptor to the FIB.

        :param descriptor: Registration-cached descriptor.
        """
        prefix = capability_lpm_prefix(descriptor.name)
        self._by_prefix.setdefault(prefix, []).append(descriptor)

    def match(
        self,
        name: Name,
        constraints: MatchConstraints | None = None,
        *,
        now: float = 0.0,
    ) -> Sequence[CapabilityDescriptor]:
        """Two-stage match for ``name``.

        :param name: Full capability name including version (or without — then
            version predicates still apply to candidates).
        :param constraints: Optional stage-2 predicates.
        :param now: Clock for reputation scoring.
        :return: Matching descriptors (possibly empty), longest-prefix first.
        """
        constraints = constraints or MatchConstraints()
        try:
            query_prefix = capability_lpm_prefix(name)
        except Exception:
            # Not a well-formed capability name — try treating all components
            # as the LPM query (no version to strip).
            query_prefix = name

        best_prefix: Name | None = None
        for prefix in self._by_prefix:
            if prefix.is_prefix_of(query_prefix):
                if best_prefix is None or len(prefix) > len(best_prefix):
                    best_prefix = prefix
        if best_prefix is None:
            return ()

        candidates = list(self._by_prefix[best_prefix])
        return tuple(
            d
            for d in candidates
            if self._passes_predicates(d, constraints, now=now)
        )

    def _passes_predicates(
        self,
        descriptor: CapabilityDescriptor,
        constraints: MatchConstraints,
        *,
        now: float,
    ) -> bool:
        c = descriptor.constraints
        if constraints.jurisdiction is not None:
            if c.get("jurisdiction") != constraints.jurisdiction:
                return False
        if constraints.max_latency_ms is not None:
            latency = c.get("latency_bound_ms")
            if type(latency) is not int or latency > constraints.max_latency_ms:
                return False
        if constraints.max_cost is not None:
            if not _cost_le(descriptor.cost, constraints.max_cost):
                return False
        if constraints.min_version is not None:
            try:
                if parse_semver(descriptor.version) < parse_semver(constraints.min_version):
                    return False
            except ValueError:
                return False
        if constraints.require_attestation:
            if descriptor.attestation_policy != "required":
                return False
        # Reputation seam: de-prioritise by filtering below threshold.
        try:
            threshold = float(descriptor.reputation_threshold)
        except ValueError:
            return False
        identity = descriptor.name.components[1]  # issuer digest
        if self.reputation.score(identity, now=now) < threshold:
            return False
        return True
