# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Aggregation policies and trace-root verification helpers.

Policies decide when a Context PIT entry may terminate. Termination always
forces remaining ``PENDING`` leaves to ``NULL_RESPONSE`` before the entry is
marked terminated, so the trace root is final at that point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from agentic.agentic_layer.context_pit import ContextPIT, ContextPitEntry
from agentic.port.errors import AggregationFailed
from agentic.trust.merkle import (
    NULL_RESPONSE,
    NULL_STEER,
    PENDING,
    inclusion_proof,
    leaf_digest,
    mth,
    verify_inclusion,
)

_QUORUM_RE = re.compile(r"^QUORUM\((\d+),(\d+)\)$")


class AggregationPolicy(Protocol):
    """Closed set of policies: ALL / QUORUM(k,n) / BEST_EFFORT."""

    name: str

    def is_satisfied(
        self,
        entry: ContextPitEntry,
        *,
        deadline_reached: bool = False,
    ) -> bool:
        """Return True when the entry may terminate under this policy."""


@dataclass(frozen=True, slots=True)
class AllPolicy:
    """Every expected leaf must be resolved (response or explicit NULL)."""

    name: str = "ALL"

    def is_satisfied(
        self,
        entry: ContextPitEntry,
        *,
        deadline_reached: bool = False,
    ) -> bool:
        return all(leaf.response_digest != PENDING for leaf in entry.leaves)


@dataclass(frozen=True, slots=True)
class QuorumPolicy:
    """At least ``k`` non-NULL responses among ``n`` expected leaves."""

    k: int
    n: int
    name: str = ""

    def __post_init__(self) -> None:
        if self.k < 1 or self.n < 1 or self.k > self.n:
            raise ValueError("QUORUM requires 1 <= k <= n")
        object.__setattr__(self, "name", f"QUORUM({self.k},{self.n})")

    def is_satisfied(
        self,
        entry: ContextPitEntry,
        *,
        deadline_reached: bool = False,
    ) -> bool:
        if len(entry.leaves) != self.n:
            return False
        successes = sum(
            1
            for leaf in entry.leaves
            if leaf.response_digest not in (PENDING, NULL_RESPONSE)
        )
        return successes >= self.k


@dataclass(frozen=True, slots=True)
class BestEffortPolicy:
    """Terminate when the deadline is reached; whatever resolved is kept."""

    name: str = "BEST_EFFORT"

    def is_satisfied(
        self,
        entry: ContextPitEntry,
        *,
        deadline_reached: bool = False,
    ) -> bool:
        return deadline_reached


def parse_aggregation_policy(label: str) -> AllPolicy | QuorumPolicy | BestEffortPolicy:
    """Parse a policy label stored on a Context PIT entry.

    :param label: ``ALL``, ``BEST_EFFORT``, or ``QUORUM(k,n)``.
    :raises ValueError: On unknown syntax.
    """
    if label == "ALL":
        return AllPolicy()
    if label == "BEST_EFFORT":
        return BestEffortPolicy()
    match = _QUORUM_RE.match(label)
    if match:
        return QuorumPolicy(k=int(match.group(1)), n=int(match.group(2)))
    raise ValueError(f"unknown aggregation_policy: {label!r}")


def resolved_count(entry: ContextPitEntry) -> int:
    """Number of leaves that are no longer ``PENDING``."""
    return sum(1 for leaf in entry.leaves if leaf.response_digest != PENDING)


def maybe_complete(
    pit: ContextPIT,
    parent_intent_digest: bytes,
    now_ms: int,
    *,
    deadline_reached: bool = False,
) -> ContextPitEntry | None:
    """Terminate the entry when its aggregation policy is satisfied.

    Forces remaining ``PENDING`` → ``NULL_RESPONSE`` inside
    :meth:`ContextPIT.terminate` before the terminated flag is set.

    :return: The terminated entry, or ``None`` if the policy is not yet met.
    """
    entry = pit.get(parent_intent_digest)
    if entry is None:
        raise KeyError("unknown parent_intent_digest")
    if entry.terminated:
        return entry
    policy = parse_aggregation_policy(entry.aggregation_policy)
    if not policy.is_satisfied(entry, deadline_reached=deadline_reached):
        return None
    return pit.terminate(parent_intent_digest, now_ms)


def fail_closed_if_unsatisfiable(
    entry: ContextPitEntry,
    *,
    deadline_reached: bool = False,
) -> None:
    """Raise :class:`AggregationFailed` when ALL/QUORUM cannot succeed.

    Call at deadline for ``ALL`` / ``QUORUM`` after forcing NULLs is the caller's
    choice; this helper only diagnoses impossibility from current leaf state.
    """
    policy = parse_aggregation_policy(entry.aggregation_policy)
    if isinstance(policy, BestEffortPolicy):
        return
    if isinstance(policy, AllPolicy):
        if deadline_reached and any(
            leaf.response_digest == PENDING for leaf in entry.leaves
        ):
            # Still pending at deadline — will become NULL on terminate; ALL
            # still "succeeds" as an accountable omission, not AggregationFailed.
            return
        return
    if isinstance(policy, QuorumPolicy):
        successes = sum(
            1
            for leaf in entry.leaves
            if leaf.response_digest not in (PENDING, NULL_RESPONSE)
        )
        pending = sum(1 for leaf in entry.leaves if leaf.response_digest == PENDING)
        if successes + pending < policy.k:
            raise AggregationFailed(
                detail=f"QUORUM({policy.k},{policy.n}) unsatisfiable"
            )


def recompute_trace_root(
    expected_subintent_set: tuple[bytes, ...],
    steer_chain_heads: tuple[bytes, ...],
    response_digests: tuple[bytes, ...],
) -> bytes:
    """Independently recompute a trace root from claimed leaf components."""
    if not (
        len(expected_subintent_set)
        == len(steer_chain_heads)
        == len(response_digests)
    ):
        raise ValueError("leaf component lengths must match")
    leaves = [
        leaf_digest(sub, steer, resp)
        for sub, steer, resp in zip(
            expected_subintent_set, steer_chain_heads, response_digests, strict=True
        )
    ]
    return mth(leaves)


def verify_claimed_root(
    expected_subintent_set: tuple[bytes, ...],
    steer_chain_heads: tuple[bytes, ...],
    response_digests: tuple[bytes, ...],
    claimed_root: bytes,
) -> bool:
    """Return True iff ``claimed_root`` matches a recomputation over the claims."""
    try:
        return (
            recompute_trace_root(
                expected_subintent_set, steer_chain_heads, response_digests
            )
            == claimed_root
        )
    except ValueError:
        return False


def verify_leaf_inclusion(
    expected_subintent_set: tuple[bytes, ...],
    steer_chain_heads: tuple[bytes, ...],
    response_digests: tuple[bytes, ...],
    leaf_index: int,
    claimed_root: bytes,
) -> bool:
    """Verify an inclusion proof for one leaf against the recomputed tree."""
    leaves = [
        leaf_digest(sub, steer, resp)
        for sub, steer, resp in zip(
            expected_subintent_set, steer_chain_heads, response_digests, strict=True
        )
    ]
    root = mth(leaves)
    if root != claimed_root:
        return False
    proof = inclusion_proof(leaves, leaf_index)
    return verify_inclusion(
        leaves[leaf_index], leaf_index, len(leaves), proof, claimed_root
    )


def default_steer_heads(n: int) -> tuple[bytes, ...]:
    """``NULL_STEER`` repeated ``n`` times (never-steered baseline)."""
    return tuple(NULL_STEER for _ in range(n))


__all__ = [
    "AllPolicy",
    "AggregationPolicy",
    "BestEffortPolicy",
    "QuorumPolicy",
    "default_steer_heads",
    "fail_closed_if_unsatisfiable",
    "maybe_complete",
    "parse_aggregation_policy",
    "recompute_trace_root",
    "resolved_count",
    "verify_claimed_root",
    "verify_leaf_inclusion",
]
