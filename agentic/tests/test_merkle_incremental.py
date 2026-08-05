# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Incremental Merkle updates must be O(log n), not a silent full rebuild."""

from __future__ import annotations

import hashlib
import math

from agentic.trust.merkle import MerkleTree, PENDING, mth


class CountingHasher:
    """Wraps SHA-256 and counts digest invocations."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, data: bytes) -> bytes:
        self.calls += 1
        return hashlib.sha256(data).digest()


def test_set_leaf_matches_full_recompute() -> None:
    leaves = [bytes([i]) for i in range(17)]
    tree = MerkleTree(leaves)
    tree.set_leaf(5, b"\xff")
    leaves[5] = b"\xff"
    assert tree.root == mth(leaves)


def test_set_leaf_hash_count_is_logarithmic() -> None:
    n = 64
    leaves = [PENDING for _ in range(n)]
    tree = MerkleTree(leaves)
    counter = CountingHasher()
    tree_counted = MerkleTree(leaves, hash_fn=counter)
    # Construction cost is paid already; reset for the update measurement.
    counter.calls = 0
    tree_counted.set_leaf(13, b"response-digest-13............")
    # Path length is ceil(log2 n) internal levels + 1 leaf hash, with some slack
    # for unbalanced splits. A full rebuild is Theta(n).
    log_bound = 4 * max(1, math.ceil(math.log2(n)))
    assert counter.calls <= log_bound
    assert counter.calls < n // 2
    assert tree_counted.root == tree.set_leaf(13, b"response-digest-13............")


def test_pending_factory() -> None:
    tree = MerkleTree.pending(4)
    assert tree.size == 4
    assert all(leaf == PENDING for leaf in tree.leaves)
