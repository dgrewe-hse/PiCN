# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Accountability log: hash chain and digest-only entries."""

from __future__ import annotations

import copy
import hashlib

import pytest

from agentic.trust import generate_ed25519_private_key
from agentic.trust.accountability_log import AccountabilityLog, ENTRY_TYPES, LogError
from agentic.trust.jcs import jcs_dumps


def test_append_and_verify_chain() -> None:
    key = generate_ed25519_private_key()
    log = AccountabilityLog(node_private_key=key)
    d0 = hashlib.sha256(b"root-0").digest()
    d1 = hashlib.sha256(b"root-1").digest()
    log.append(
        entry_type="trace_root_anchored",
        subject="intent-a",
        payload_digest=d0,
        at_ms=1,
    )
    log.append(
        entry_type="verification_outcome",
        subject="intent-a",
        payload_digest=d1,
        at_ms=2,
    )
    assert log.length == 2
    log.verify_chain()
    bodies = [e["body"] for e in log.entries()]
    assert all("payload_digest" in b for b in bodies)
    assert all("payload" not in b for b in bodies)


def test_tamper_breaks_chain() -> None:
    key = generate_ed25519_private_key()
    log = AccountabilityLog(node_private_key=key)
    log.append(
        entry_type="quote_issued",
        subject="q1",
        payload_digest=hashlib.sha256(b"q").digest(),
        at_ms=1,
    )
    log.append(
        entry_type="claim_recorded",
        subject="q1",
        payload_digest=hashlib.sha256(b"c").digest(),
        at_ms=2,
    )
    # Mutate first entry's subject after the fact (breaks signature and/or chain).
    tampered = copy.deepcopy(log._entries[0])
    tampered["body"]["subject"] = "evil"
    log._entries[0] = tampered
    with pytest.raises(LogError):
        log.verify_chain()


def test_prev_hash_covers_canonical_bytes() -> None:
    key = generate_ed25519_private_key()
    log = AccountabilityLog(node_private_key=key)
    env0 = log.append(
        entry_type="reputation_updated",
        subject="agent-1",
        payload_digest=hashlib.sha256(b"rep").digest(),
        at_ms=10,
    )
    env1 = log.append(
        entry_type="trace_root_anchored",
        subject="intent-1",
        payload_digest=hashlib.sha256(b"root").digest(),
        at_ms=11,
    )
    canonical0 = jcs_dumps(env0["body"])
    expected_prev = hashlib.sha256(canonical0).digest()
    import base64

    padding = "=" * (-len(env1["body"]["prev_hash"]) % 4)
    got = base64.urlsafe_b64decode(env1["body"]["prev_hash"] + padding)
    assert got == expected_prev


def test_unknown_type_rejected() -> None:
    key = generate_ed25519_private_key()
    log = AccountabilityLog(node_private_key=key)
    with pytest.raises(LogError, match="unknown entry type"):
        log.append(
            entry_type="packet_sent",
            subject="x",
            payload_digest=hashlib.sha256(b"x").digest(),
            at_ms=1,
        )
    assert "packet_sent" not in ENTRY_TYPES


def test_file_persistence_roundtrip(tmp_path) -> None:
    key = generate_ed25519_private_key()
    path = tmp_path / "log.jsonl"
    log = AccountabilityLog(node_private_key=key, path=path)
    log.append(
        entry_type="trace_root_anchored",
        subject="i",
        payload_digest=hashlib.sha256(b"r").digest(),
        at_ms=1,
    )
    reloaded = AccountabilityLog(node_private_key=key, path=path)
    assert reloaded.length == 1
    reloaded.verify_chain()


def test_append_rejects_bad_digest_and_time() -> None:
    key = generate_ed25519_private_key()
    log = AccountabilityLog(node_private_key=key)
    with pytest.raises(LogError, match="payload_digest"):
        log.append(
            entry_type="quote_issued",
            subject="s",
            payload_digest=b"short",
            at_ms=1,
        )
    with pytest.raises(LogError, match="at_ms"):
        log.append(
            entry_type="quote_issued",
            subject="s",
            payload_digest=hashlib.sha256(b"x").digest(),
            at_ms=-1,
        )
