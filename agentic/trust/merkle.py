# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""RFC 6962 Merkle tree for the agentic trace root.

SHA-256 with ``0x00`` / ``0x01`` domain separation. Split at the largest
power of two strictly less than ``n``. Leaf count is fixed at construction;
updates recompute only the path to the root.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Final

HashFn = Callable[[bytes], bytes]


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


# Domain-separated sentinels (A-005). Not zero-bytes or empty digests.
NULL_RESPONSE: Final[bytes] = _sha256(b"agentic/null-response/v1")
NULL_STEER: Final[bytes] = _sha256(b"agentic/null-steer/v1")
PENDING: Final[bytes] = _sha256(b"agentic/pending/v1")

_EMPTY_ROOT: Final[bytes] = _sha256(b"")


def leaf_digest(
    sub_intent_digest: bytes,
    steer_chain_head: bytes,
    response_digest: bytes,
    *,
    hash_fn: HashFn = _sha256,
) -> bytes:
    """Compute one trace-leaf value.

    :param sub_intent_digest: Canonical sub-intent digest.
    :param steer_chain_head: Steer hash-chain head (``NULL_STEER`` if never steered).
    :param response_digest: Response digest, ``PENDING``, or ``NULL_RESPONSE``.
    :param hash_fn: Hash primitive (injectable for tests).
    :return: ``SHA-256(sub || steer || response)``.
    """
    return hash_fn(sub_intent_digest + steer_chain_head + response_digest)


def largest_power_of_two_lt(n: int) -> int:
    """Largest power of two strictly less than ``n`` (RFC 6962 split point).

    :param n: Subtree size; must be ``>= 2``.
    :return: Split index ``k``.
    """
    if n < 2:
        raise ValueError("n must be >= 2")
    return 1 << ((n - 1).bit_length() - 1)


def mth(leaves: Sequence[bytes], *, hash_fn: HashFn = _sha256) -> bytes:
    """Merkle Tree Hash over an ordered leaf sequence (RFC 6962 §2.1).

    :param leaves: Leaf *data* values (hashed with the ``0x00`` prefix).
    :param hash_fn: Hash primitive.
    :return: Tree root.
    """
    n = len(leaves)
    if n == 0:
        return hash_fn(b"")
    if n == 1:
        return hash_fn(b"\x00" + leaves[0])
    k = largest_power_of_two_lt(n)
    return hash_fn(b"\x01" + mth(leaves[:k], hash_fn=hash_fn) + mth(leaves[k:], hash_fn=hash_fn))


def inclusion_proof(
    leaves: Sequence[bytes],
    index: int,
    *,
    hash_fn: HashFn = _sha256,
) -> list[bytes]:
    """RFC 6962 Merkle audit path for ``leaves[index]``.

    :param leaves: Full ordered leaf data.
    :param index: Leaf index in ``[0, n)``.
    :param hash_fn: Hash primitive.
    :return: Ordered sibling hashes from the leaf toward the root.
    """
    n = len(leaves)
    if n == 0:
        raise ValueError("empty tree has no inclusion proof")
    if index < 0 or index >= n:
        raise IndexError("leaf index out of range")
    return _path(index, leaves, hash_fn)


def _path(m: int, leaves: Sequence[bytes], hash_fn: HashFn) -> list[bytes]:
    n = len(leaves)
    if n == 1:
        return []
    k = largest_power_of_two_lt(n)
    if m < k:
        return _path(m, leaves[:k], hash_fn) + [mth(leaves[k:], hash_fn=hash_fn)]
    return _path(m - k, leaves[k:], hash_fn) + [mth(leaves[:k], hash_fn=hash_fn)]


def verify_inclusion(
    leaf: bytes,
    index: int,
    tree_size: int,
    proof: Sequence[bytes],
    root: bytes,
    *,
    hash_fn: HashFn = _sha256,
) -> bool:
    """Verify an RFC 6962 inclusion proof against ``root``.

    :param leaf: Leaf *data* (same value stored in the tree).
    :param index: Claimed leaf index.
    :param tree_size: Number of leaves when the proof was generated.
    :param proof: Audit path from :func:`inclusion_proof`.
    :param root: Expected Merkle root.
    :param hash_fn: Hash primitive.
    :return: True iff the proof reconstructs ``root``.
    """
    if tree_size <= 0 or index < 0 or index >= tree_size:
        return False
    try:
        calculated = _root_from_inclusion_proof(
            leaf, index, tree_size, list(proof), hash_fn
        )
    except ValueError:
        return False
    return calculated == root


def _root_from_inclusion_proof(
    leaf: bytes,
    index: int,
    size: int,
    proof: list[bytes],
    hash_fn: HashFn,
) -> bytes:
    """Reconstruct the root; ``proof`` siblings are appended outer-last (RFC PATH)."""
    if size == 1:
        if proof:
            raise ValueError("unused proof elements")
        return hash_fn(b"\x00" + leaf)
    k = largest_power_of_two_lt(size)
    if not proof:
        raise ValueError("proof too short")
    sibling = proof[-1]
    head = proof[:-1]
    if index < k:
        left = _root_from_inclusion_proof(leaf, index, k, head, hash_fn)
        return hash_fn(b"\x01" + left + sibling)
    right = _root_from_inclusion_proof(leaf, index - k, size - k, head, hash_fn)
    return hash_fn(b"\x01" + sibling + right)


class MerkleTree:
    """Fixed-size RFC 6962 Merkle tree with path-local updates.

    :param leaves: Initial leaf data; length is immutable.
    :param hash_fn: Hash primitive (injectable so tests can count invocations).
    """

    def __init__(
        self,
        leaves: Sequence[bytes],
        *,
        hash_fn: HashFn = _sha256,
    ) -> None:
        self._hash_fn = hash_fn
        self._leaves: list[bytes] = list(leaves)
        self._size = len(self._leaves)
        # Cached MTH for half-open ranges [lo, hi).
        self._cache: dict[tuple[int, int], bytes] = {}
        self._root = self._mth_range(0, self._size)

    @classmethod
    def pending(cls, size: int, *, hash_fn: HashFn = _sha256) -> MerkleTree:
        """Build a tree of ``size`` leaves, each set to :data:`PENDING`."""
        if size < 0:
            raise ValueError("size must be non-negative")
        return cls([PENDING] * size, hash_fn=hash_fn)

    @property
    def size(self) -> int:
        return self._size

    @property
    def root(self) -> bytes:
        return self._root

    @property
    def leaves(self) -> tuple[bytes, ...]:
        return tuple(self._leaves)

    def get_leaf(self, index: int) -> bytes:
        if index < 0 or index >= self._size:
            raise IndexError("leaf index out of range")
        return self._leaves[index]

    def set_leaf(self, index: int, value: bytes) -> bytes:
        """Update one leaf and recompute only its path to the root.

        :param index: Leaf index.
        :param value: New leaf data.
        :return: New root.
        """
        if index < 0 or index >= self._size:
            raise IndexError("leaf index out of range")
        self._leaves[index] = value
        self._root = self._mth_range_update(0, self._size, index)
        return self._root

    def inclusion_proof(self, index: int) -> list[bytes]:
        """Audit path for ``index`` using cached sibling subtrees where possible."""
        return inclusion_proof(self._leaves, index, hash_fn=self._hash_fn)

    def _mth_range(self, lo: int, hi: int) -> bytes:
        key = (lo, hi)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        n = hi - lo
        if n == 0:
            digest = self._hash_fn(b"")
        elif n == 1:
            digest = self._hash_fn(b"\x00" + self._leaves[lo])
        else:
            k = largest_power_of_two_lt(n)
            mid = lo + k
            digest = self._hash_fn(
                b"\x01" + self._mth_range(lo, mid) + self._mth_range(mid, hi)
            )
        self._cache[key] = digest
        return digest

    def _mth_range_update(self, lo: int, hi: int, update_idx: int) -> bytes:
        """Recompute MTH for ``[lo, hi)`` touching only the path through ``update_idx``."""
        n = hi - lo
        if n == 0:
            digest = self._hash_fn(b"")
            self._cache[(lo, hi)] = digest
            return digest
        if n == 1:
            digest = self._hash_fn(b"\x00" + self._leaves[lo])
            self._cache[(lo, hi)] = digest
            return digest
        k = largest_power_of_two_lt(n)
        mid = lo + k
        if update_idx < mid:
            left = self._mth_range_update(lo, mid, update_idx)
            right = self._mth_range(mid, hi)
        else:
            left = self._mth_range(lo, mid)
            right = self._mth_range_update(mid, hi, update_idx)
        digest = self._hash_fn(b"\x01" + left + right)
        self._cache[(lo, hi)] = digest
        return digest


__all__ = [
    "NULL_RESPONSE",
    "NULL_STEER",
    "PENDING",
    "MerkleTree",
    "inclusion_proof",
    "largest_power_of_two_lt",
    "leaf_digest",
    "mth",
    "verify_inclusion",
]
