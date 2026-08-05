# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Context PIT — committed expected sets and Merkle trace roots.

Invariant I2: the expected sub-intent set is committed before any sub-intent is
forwarded. In-flight entries are never evicted; at capacity the PIT refuses new
intents with :class:`~agentic.port.errors.CapacityExhausted`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentic.port.errors import CapacityExhausted
from agentic.trust.merkle import (
    NULL_RESPONSE,
    NULL_STEER,
    PENDING,
    MerkleTree,
    leaf_digest,
)

# PROVISIONAL: bound concurrent in-flight intents; derive from CH2 / sweep data.
MAX_CONTEXT_PIT_ENTRIES: int = 256

# PROVISIONAL: how long terminated entries remain for late/audit queries.
RESULT_RETENTION_MS: int = 60_000


@dataclass
class ContextPitLeaf:
    """One committed sub-intent leaf and its mutable runtime fields.

    :param sub_intent_digest: Canonical digest (fixed at commit; leaf order key).
    :param payload: Current request payload (may be steered).
    :param latency_bound: Current latency bound (may be steered).
    :param capability: Immutable capability path.
    :param credential: Immutable credential blob.
    :param response_digest: ``PENDING``, a response digest, or ``NULL_RESPONSE``.
    :param steer_count: Steers applied to this leaf.
    :param steer_chain_head: Steer hash-chain head (``NULL_STEER`` if never steered).
    :param forwarded: Set when the sub-intent has been handed to the substrate.
    """

    sub_intent_digest: bytes
    payload: bytes
    latency_bound: int | None
    capability: tuple[str, ...]
    credential: bytes
    response_digest: bytes = field(default_factory=lambda: PENDING)
    steer_count: int = 0
    steer_chain_head: bytes = field(default_factory=lambda: NULL_STEER)
    forwarded: bool = False

    def merkle_leaf_value(self) -> bytes:
        """Trace-leaf value incorporating steer chain and response."""
        return leaf_digest(
            self.sub_intent_digest,
            self.steer_chain_head,
            self.response_digest,
        )


@dataclass
class ContextPitEntry:
    """One parent-intent Context PIT entry.

    :param parent_intent_digest: Entry key (opaque correlation / intent digest).
    :param issuer_public_key_der: Original intent issuer (Steer authorisation).
    :param aggregation_policy: Policy name; immutable under Steer.
    :param expected_subintent_set: Canonically ordered sub-intent digests.
    :param leaves: Per-leaf runtime state aligned with Merkle indices.
    :param tree: Fixed-size Merkle tree over :meth:`ContextPitLeaf.merkle_leaf_value`.
    :param committed: True once the expected set is written (I2).
    :param terminated: True after aggregation completion / forced NULL padding.
    :param terminated_at_ms: Clock reading at termination (for retention/eviction).
    """

    parent_intent_digest: bytes
    issuer_public_key_der: bytes
    aggregation_policy: str
    expected_subintent_set: tuple[bytes, ...]
    leaves: list[ContextPitLeaf]
    tree: MerkleTree
    committed: bool = True
    terminated: bool = False
    terminated_at_ms: int | None = None

    @property
    def trace_root(self) -> bytes:
        return self.tree.root

    def assert_committed_before_forward(self) -> None:
        """Raise if any leaf was forwarded before commit (I2 guard)."""
        if not self.committed:
            raise RuntimeError("forward before Context PIT commit")


class ContextPIT:
    """In-memory Context PIT with capacity refusal (never evicts in-flight).

    :param max_entries: Capacity bound (``MAX_CONTEXT_PIT_ENTRIES`` by default).
    :param result_retention_ms: Soft retention for terminated entries.
    """

    def __init__(
        self,
        *,
        max_entries: int = MAX_CONTEXT_PIT_ENTRIES,
        result_retention_ms: int = RESULT_RETENTION_MS,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._result_retention_ms = result_retention_ms
        self._entries: dict[bytes, ContextPitEntry] = {}
        self.context_pit_refusals: int = 0

    @property
    def context_pit_entries(self) -> int:
        """Current number of retained entries (in-flight and terminated)."""
        return len(self._entries)

    def get(self, parent_intent_digest: bytes) -> ContextPitEntry | None:
        return self._entries.get(parent_intent_digest)

    def commit(
        self,
        *,
        parent_intent_digest: bytes,
        issuer_public_key_der: bytes,
        aggregation_policy: str,
        leaf_specs: list[tuple[bytes, bytes, int | None, tuple[str, ...], bytes]],
        now_ms: int,
    ) -> ContextPitEntry:
        """Commit the expected set and all-PENDING Merkle tree **before** forwarding.

        :param parent_intent_digest: Entry key.
        :param issuer_public_key_der: Intent issuer SPKI DER.
        :param aggregation_policy: Policy label (interpreted in aggregation).
        :param leaf_specs: ``(digest, payload, latency_bound, capability, credential)``.
        :param now_ms: Injected clock (ms) for retention / eviction.
        :return: The committed entry.
        :raises CapacityExhausted: When full and no terminated entry can be evicted.
        """
        if parent_intent_digest in self._entries:
            raise ValueError("parent_intent_digest already present")

        self._reap_expired(now_ms)
        if len(self._entries) >= self._max_entries:
            if not self._evict_oldest_terminated():
                self.context_pit_refusals += 1
                raise CapacityExhausted(
                    detail="Context PIT at capacity with no terminated entries"
                )

        ordered = sorted(leaf_specs, key=lambda spec: spec[0])
        leaves = [
            ContextPitLeaf(
                sub_intent_digest=digest,
                payload=payload,
                latency_bound=latency_bound,
                capability=capability,
                credential=credential,
            )
            for digest, payload, latency_bound, capability, credential in ordered
        ]
        tree = MerkleTree([leaf.merkle_leaf_value() for leaf in leaves])
        entry = ContextPitEntry(
            parent_intent_digest=parent_intent_digest,
            issuer_public_key_der=issuer_public_key_der,
            aggregation_policy=aggregation_policy,
            expected_subintent_set=tuple(leaf.sub_intent_digest for leaf in leaves),
            leaves=leaves,
            tree=tree,
            committed=True,
        )
        self._entries[parent_intent_digest] = entry
        return entry

    def mark_forwarded(self, parent_intent_digest: bytes, leaf_index: int) -> None:
        """Record that a committed leaf has been forwarded (I2 workflow hook)."""
        entry = self._require(parent_intent_digest)
        entry.assert_committed_before_forward()
        leaf = entry.leaves[leaf_index]
        leaf.forwarded = True

    def record_response(
        self,
        parent_intent_digest: bytes,
        leaf_index: int,
        response_digest: bytes,
    ) -> bytes:
        """Place a response digest at ``leaf_index``; never prune the leaf.

        :return: Updated trace root.
        """
        entry = self._require(parent_intent_digest)
        if entry.terminated:
            raise ValueError("entry already terminated")
        leaf = entry.leaves[leaf_index]
        leaf.response_digest = response_digest
        return entry.tree.set_leaf(leaf_index, leaf.merkle_leaf_value())

    def record_timeout(self, parent_intent_digest: bytes, leaf_index: int) -> bytes:
        """Record an explicit NULL leaf for a timed-out sub-intent (I3)."""
        return self.record_response(parent_intent_digest, leaf_index, NULL_RESPONSE)

    def refresh_leaf_merkle(self, entry: ContextPitEntry, leaf_index: int) -> bytes:
        """Recompute one Merkle leaf after a Steer (path-local update)."""
        leaf = entry.leaves[leaf_index]
        return entry.tree.set_leaf(leaf_index, leaf.merkle_leaf_value())

    def terminate(self, parent_intent_digest: bytes, now_ms: int) -> ContextPitEntry:
        """Force remaining PENDING leaves to NULL, then mark terminated.

        The trace root is final after this call. PENDING→NULL happens **before**
        the terminated flag is set.
        """
        entry = self._require(parent_intent_digest)
        if entry.terminated:
            return entry
        for index, leaf in enumerate(entry.leaves):
            if leaf.response_digest == PENDING:
                leaf.response_digest = NULL_RESPONSE
                entry.tree.set_leaf(index, leaf.merkle_leaf_value())
        entry.terminated = True
        entry.terminated_at_ms = now_ms
        return entry

    def _require(self, parent_intent_digest: bytes) -> ContextPitEntry:
        entry = self._entries.get(parent_intent_digest)
        if entry is None:
            raise KeyError("unknown parent_intent_digest")
        return entry

    def _reap_expired(self, now_ms: int) -> None:
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.terminated
            and entry.terminated_at_ms is not None
            and now_ms - entry.terminated_at_ms >= self._result_retention_ms
        ]
        for key in expired:
            del self._entries[key]

    def _evict_oldest_terminated(self) -> bool:
        """Evict one terminated entry (oldest-terminated first). Never in-flight."""
        candidates = [
            (entry.terminated_at_ms, key)
            for key, entry in self._entries.items()
            if entry.terminated and entry.terminated_at_ms is not None
        ]
        if not candidates:
            return False
        candidates.sort()
        _, key = candidates[0]
        del self._entries[key]
        return True


__all__ = [
    "MAX_CONTEXT_PIT_ENTRIES",
    "RESULT_RETENTION_MS",
    "ContextPIT",
    "ContextPitEntry",
    "ContextPitLeaf",
]
