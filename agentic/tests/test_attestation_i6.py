# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Invariant I6: valid attestation coexists with a false capacity claim."""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.trust.attestation import (
    ClaimRecord,
    VerifiedQuote,
    issue_quote,
    node_id_from_key,
    verify_quote,
)
from agentic.trust.reputation import WEIGHT_UNVERIFIED, ReputationTable
from agentic.trust import generate_ed25519_private_key


def test_verified_quote_has_no_claim_trust_flag() -> None:
    """Result type must not grow a trusted/valid_claim boolean (A-008)."""
    assert not hasattr(VerifiedQuote, "trusted")
    assert not hasattr(VerifiedQuote, "valid_claim")
    fields = set(VerifiedQuote.__dataclass_fields__)
    assert "trusted" not in fields
    assert "valid_claim" not in fields


def test_i6_valid_quote_with_false_claim_enters_reputation_unverified() -> None:
    """Genuine quote + lying capacity claim remain distinguishable.

    Verification passes for the quote; the claim is recorded separately and
    updates reputation only as an unverified assertion. A later mismatch
    lowers the score — the quote is never used as a fast-path for truth.
    """
    node_key = generate_ed25519_private_key()
    public_der = node_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    node_id = node_id_from_key(public_der)
    binary = hashlib.sha256(b"agent-binary-v1").digest()

    quote_env = issue_quote(
        node_private_key=node_key,
        binary_hash=binary,
        issued_at_ms=1_000,
        valid_for_s=300,
        nonce=b"\x11" * 32,
    )
    verified = verify_quote(quote_env, expected_node_id=node_id, now_ms=1_000)
    assert verified.quote_id == quote_env["body"]["quote_id"]
    assert verified.node_id == node_id

    # False capacity claim — separate object, references quote_id only.
    false_claim = ClaimRecord(
        quote_id=verified.quote_id,
        claim=b'{"beds_free": 12}',  # lie; ground truth will be 0
        agent_id=public_der,
        capability_domain="hospital/beds",
    )
    assert false_claim.quote_id == verified.quote_id
    # Response shape: quote_id reference, not an embedded quote blob.
    response = {"quote_id": false_claim.quote_id, "body": false_claim.claim}
    assert "attestation-quote" not in str(response["body"])
    assert "sig" not in response

    table = ReputationTable()
    # Unverified assertion while quote itself verified (I6 boundary).
    mean_after_claim, _, obs = table.observe(
        false_claim.agent_id,
        false_claim.capability_domain,
        success=True,
        weight=WEIGHT_UNVERIFIED,
        now=1.0,
    )
    assert obs == 1
    assert 0.5 < mean_after_claim < 1.0

    # Ground-truth mismatch detection: claim was false → negative observation.
    ground_truth_beds = 0
    claimed_beds = 12
    assert claimed_beds != ground_truth_beds
    mean_after_mismatch, _, obs2 = table.observe(
        false_claim.agent_id,
        false_claim.capability_domain,
        success=False,
        weight=1.0,  # attested / cross-verified mismatch
        now=2.0,
    )
    assert obs2 == 2
    assert mean_after_mismatch < mean_after_claim
    # Quote still verifies — attestation did not become claim validation.
    still = verify_quote(quote_env, expected_node_id=node_id, now_ms=2_000)
    assert still.quote_id == verified.quote_id


def test_stale_quote_rejected() -> None:
    node_key = generate_ed25519_private_key()
    public_der = node_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    env = issue_quote(
        node_private_key=node_key,
        binary_hash=b"\xaa" * 32,
        issued_at_ms=0,
        valid_for_s=1,
    )
    import pytest
    from agentic.trust.attestation import QuoteError

    with pytest.raises(QuoteError, match="freshness"):
        verify_quote(
            env,
            expected_node_id=node_id_from_key(public_der),
            now_ms=2_000,
        )


def test_wrong_node_id_rejected() -> None:
    import pytest
    from agentic.trust.attestation import QuoteError

    node_key = generate_ed25519_private_key()
    env = issue_quote(
        node_private_key=node_key,
        binary_hash=b"\xbb" * 32,
        issued_at_ms=0,
        valid_for_s=60,
        nonce=b"\x22" * 32,
    )
    with pytest.raises(QuoteError, match="node_id"):
        verify_quote(env, expected_node_id="not-this-node", now_ms=0)


def test_issue_quote_rejects_bad_inputs() -> None:
    import pytest
    from agentic.trust.attestation import QuoteError

    key = generate_ed25519_private_key()
    with pytest.raises(QuoteError):
        issue_quote(node_private_key=key, binary_hash=b"short", issued_at_ms=0)
    with pytest.raises(QuoteError):
        issue_quote(
            node_private_key=key,
            binary_hash=b"\xcc" * 32,
            issued_at_ms=-1,
        )
    with pytest.raises(QuoteError):
        issue_quote(
            node_private_key=key,
            binary_hash=b"\xcc" * 32,
            issued_at_ms=0,
            valid_for_s=0,
        )
    with pytest.raises(QuoteError):
        issue_quote(
            node_private_key=key,
            binary_hash=b"\xcc" * 32,
            issued_at_ms=0,
            nonce=b"\x01",
        )
