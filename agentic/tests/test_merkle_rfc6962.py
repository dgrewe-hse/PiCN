# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""RFC 6962 conformance tests for the agentic Merkle tree."""

from __future__ import annotations

import hashlib

import pytest

from agentic.trust.merkle import (
    NULL_RESPONSE,
    NULL_STEER,
    PENDING,
    MerkleTree,
    inclusion_proof,
    largest_power_of_two_lt,
    leaf_digest,
    mth,
    verify_inclusion,
)

# Published / well-known RFC 6962 vectors (leaf data = single bytes 0x00..).
# Empty root is stated in RFC 6962 §2.1; multi-leaf roots match the CT reference
# construction used across Google CT / Trillian test suites.
RFC6962_EMPTY_ROOT = bytes.fromhex(
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
RFC6962_ROOTS = {
    1: bytes.fromhex(
        "96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7"
    ),
    2: bytes.fromhex(
        "a20bf9a7cc2dc8a08f5f415a71b19f6ac427bab54d24eec868b5d3103449953a"
    ),
    3: bytes.fromhex(
        "3b6cccd7e3e023ff393006f030315ee7ad9eb111b022b41fba7e5b7a3973f688"
    ),
    4: bytes.fromhex(
        "9bcd51240af4005168f033121ba85be5a6ed4f0e6a5fac262066729b8fbfdecb"
    ),
    5: bytes.fromhex(
        "b855b42d6c30f5b087e05266783fbd6e394f7b926013ccaa67700a8b0c5a596f"
    ),
    6: bytes.fromhex(
        "bb36e7d3d4cee5720cbd323d02fab15962e2ba1dadf5f8fc6eeef4fd6ad056a8"
    ),
    7: bytes.fromhex(
        "3560191803028444b232018ac047fdb561c09c23a7a6876c85e08b5e4d48e9f3"
    ),
}


def test_rfc6962_empty_tree_vector() -> None:
    assert mth([]) == RFC6962_EMPTY_ROOT
    assert MerkleTree([]).root == RFC6962_EMPTY_ROOT


@pytest.mark.parametrize("n", sorted(RFC6962_ROOTS))
def test_rfc6962_published_roots_for_byte_leaves(n: int) -> None:
    leaves = [bytes([i]) for i in range(n)]
    assert mth(leaves) == RFC6962_ROOTS[n]
    assert MerkleTree(leaves).root == RFC6962_ROOTS[n]


def test_single_empty_leaf_is_sha256_of_0x00() -> None:
    expected = hashlib.sha256(b"\x00").digest()
    assert mth([b""]) == expected


def test_domain_separation_prefixes_present() -> None:
    """Leaf and internal hashes must use 0x00 / 0x01 (not bare concatenations)."""
    leaf = b"\x00"
    leaf_hash = hashlib.sha256(b"\x00" + leaf).digest()
    assert mth([leaf]) == leaf_hash
    # Two leaves: internal node = SHA-256(0x01 || L || R)
    left = hashlib.sha256(b"\x00" + b"\x00").digest()
    right = hashlib.sha256(b"\x00" + b"\x01").digest()
    expected = hashlib.sha256(b"\x01" + left + right).digest()
    assert mth([b"\x00", b"\x01"]) == expected
    assert expected != hashlib.sha256(left + right).digest()


def test_split_at_largest_power_of_two_strictly_less_than_n() -> None:
    assert largest_power_of_two_lt(2) == 1
    assert largest_power_of_two_lt(3) == 2
    assert largest_power_of_two_lt(4) == 2
    assert largest_power_of_two_lt(5) == 4
    assert largest_power_of_two_lt(8) == 4


def test_sentinels_are_domain_separated_hashes() -> None:
    assert NULL_RESPONSE == hashlib.sha256(b"agentic/null-response/v1").digest()
    assert NULL_STEER == hashlib.sha256(b"agentic/null-steer/v1").digest()
    assert PENDING == hashlib.sha256(b"agentic/pending/v1").digest()
    assert len({NULL_RESPONSE, NULL_STEER, PENDING}) == 3


def test_leaf_digest_formula() -> None:
    sub = b"\x11" * 32
    steer = NULL_STEER
    resp = PENDING
    assert leaf_digest(sub, steer, resp) == hashlib.sha256(sub + steer + resp).digest()


@pytest.mark.parametrize("n", range(0, 65))
def test_inclusion_proof_verifies_for_every_index(n: int) -> None:
    leaves = [bytes([i % 256]) + bytes([n]) + i.to_bytes(2, "big") for i in range(n)]
    root = mth(leaves)
    tree = MerkleTree(leaves)
    assert tree.root == root
    for i in range(n):
        proof = inclusion_proof(leaves, i)
        assert verify_inclusion(leaves[i], i, n, proof, root)
        assert verify_inclusion(leaves[i], i, n, tree.inclusion_proof(i), tree.root)


def test_tampered_leaf_fails_inclusion() -> None:
    leaves = [bytes([i]) for i in range(5)]
    root = mth(leaves)
    proof = inclusion_proof(leaves, 2)
    assert not verify_inclusion(b"\xff", 2, 5, proof, root)
