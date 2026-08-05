# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Simulated attestation quotes (node-scoped, referenced by id).

Verification answers only: this endpoint ran this binary, recently. It does
**not** speak to claim truth (invariant I6). Responses must reference
``quote_id`` and never embed a quote (AC6).

Simulation caveat: there is no hardware root of trust in v0.1 — quotes prove
cryptographic shape only.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.trust.jcs import jcs_dumps
from agentic.trust.signed_artefact import (
    ArtefactVerificationError,
    sign_artefact,
    verify_artefact,
)

# PROVISIONAL: quote freshness window; shorten to trade cost for freshness (A-008).
DEFAULT_VALID_FOR_S: int = 300


class QuoteError(ValueError):
    """Raised when a quote cannot be built or verified."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def node_id_from_key(public_key_der: bytes) -> str:
    """Issuer-digest style node id: lowercase unpadded base32(SHA-256(DER))."""
    digest = hashlib.sha256(public_key_der).digest()
    return base64.b32encode(digest).decode("ascii").lower().rstrip("=")


@dataclass(frozen=True, slots=True)
class VerifiedQuote:
    """Result of successful quote verification — runtime evidence only.

    Deliberately has **no** ``trusted`` / ``valid_claim`` field: verification
    never speaks to the truth of a capacity claim (I6).
    """

    quote_id: str
    node_id: str
    binary_hash: str
    issued_at_ms: int
    valid_for_s: int
    public_key_der: bytes


def issue_quote(
    *,
    node_private_key: Ed25519PrivateKey,
    binary_hash: bytes,
    issued_at_ms: int,
    valid_for_s: int = DEFAULT_VALID_FOR_S,
    nonce: bytes | None = None,
) -> dict[str, Any]:
    """Mint a node-scoped attestation quote envelope (trust layer only).

    Binding code must not call this (AC6) — quotes are issued by the node /
    trust substrate and referenced by id from responses.

    :param node_private_key: Node signing key.
    :param binary_hash: SHA-256 of the attested binary image.
    :param issued_at_ms: Issue time (injected clock, integer ms).
    :param valid_for_s: Freshness window in seconds.
    :param nonce: Optional 32-byte nonce; random if omitted.
    :return: Signed artefact envelope with ``kind=attestation-quote``.
    """
    if type(issued_at_ms) is not int or isinstance(issued_at_ms, bool) or issued_at_ms < 0:
        raise QuoteError("issued_at_ms must be a non-negative int")
    if type(valid_for_s) is not int or isinstance(valid_for_s, bool) or valid_for_s <= 0:
        raise QuoteError("valid_for_s must be a positive int")
    if len(binary_hash) != 32:
        raise QuoteError("binary_hash must be 32 bytes")
    nonce_bytes = nonce if nonce is not None else os.urandom(32)
    if len(nonce_bytes) != 32:
        raise QuoteError("nonce must be 32 bytes")

    public_der = node_private_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    body_without_id: dict[str, Any] = {
        "kind": "attestation-quote",
        "node_id": node_id_from_key(public_der),
        "binary_hash": _b64url_encode(binary_hash),
        "nonce": _b64url_encode(nonce_bytes),
        "issued_at_ms": issued_at_ms,
        "valid_for_s": valid_for_s,
    }
    quote_id = _b64url_encode(hashlib.sha256(jcs_dumps(body_without_id)).digest())
    body = {**body_without_id, "quote_id": quote_id}
    return sign_artefact(body, node_private_key)


def verify_quote(
    envelope: Mapping[str, Any],
    *,
    expected_node_id: str,
    now_ms: int,
) -> VerifiedQuote:
    """Verify signature, freshness, and node id match.

    Returns runtime evidence only. Does **not** validate any capacity claim.

    :param envelope: Signed quote envelope.
    :param expected_node_id: Node id of the responding endpoint.
    :param now_ms: Injected clock (ms).
    :return: :class:`VerifiedQuote` on success.
    :raises QuoteError: On any verification failure.
    """
    try:
        artefact = verify_artefact(envelope)
    except (ArtefactVerificationError, Exception) as exc:
        raise QuoteError(f"quote signature verification failed: {exc}") from exc
    if artefact.kind != "attestation-quote":
        raise QuoteError("not an attestation-quote")
    body = dict(artefact.body)
    try:
        quote_id = str(body["quote_id"])
        node_id = str(body["node_id"])
        binary_hash_b64 = str(body["binary_hash"])
        issued_at_ms = body["issued_at_ms"]
        valid_for_s = body["valid_for_s"]
    except KeyError as exc:
        raise QuoteError(f"quote missing field: {exc}") from exc
    if type(issued_at_ms) is not int or isinstance(issued_at_ms, bool):
        raise QuoteError("issued_at_ms must be an int")
    if type(valid_for_s) is not int or isinstance(valid_for_s, bool):
        raise QuoteError("valid_for_s must be an int")
    if node_id != expected_node_id:
        raise QuoteError("node_id does not match responding endpoint")
    expires_at = issued_at_ms + valid_for_s * 1000
    if now_ms > expires_at:
        raise QuoteError("quote freshness window elapsed")
    # Re-check quote_id binding
    without_id = {k: v for k, v in body.items() if k != "quote_id"}
    expected_id = _b64url_encode(hashlib.sha256(jcs_dumps(without_id)).digest())
    if quote_id != expected_id:
        raise QuoteError("quote_id does not match body")
    return VerifiedQuote(
        quote_id=quote_id,
        node_id=node_id,
        binary_hash=binary_hash_b64,
        issued_at_ms=issued_at_ms,
        valid_for_s=valid_for_s,
        public_key_der=artefact.public_key_der,
    )


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    """An endpoint claim kept separate from quote verification (I6).

    :param quote_id: Reference only — never an embedded quote.
    :param claim: Opaque claim bytes (e.g. advertised capacity).
    :param verified_against_ground_truth: Filled later by mismatch detection.
    """

    quote_id: str
    claim: bytes
    agent_id: bytes
    capability_domain: str


__all__ = [
    "DEFAULT_VALID_FOR_S",
    "ClaimRecord",
    "QuoteError",
    "VerifiedQuote",
    "issue_quote",
    "node_id_from_key",
    "verify_quote",
]
