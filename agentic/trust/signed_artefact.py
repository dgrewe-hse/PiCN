# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Signed artefact envelope: JCS body + detached Ed25519 signature.

One envelope for capability descriptors and task-graph templates. Signing is
always over ``JCS(body)`` — never over non-canonical JSON serialization.
Verify before any field of the body is read.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_der_public_key,
)

from agentic.trust.jcs import FloatInSignedBodyError, jcs_dumps

ALLOWED_KINDS = frozenset(
    {
        "capability-descriptor",
        "task-graph-template",
        "attestation-quote",
        "accountability-log-entry",
    }
)


class ArtefactError(ValueError):
    """Base error for signed-artefact construction or verification failures."""


class ArtefactVerificationError(ArtefactError):
    """Raised when signature verification fails or the envelope is malformed."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


@dataclass(frozen=True, slots=True)
class SignedArtefact:
    """Verified signed artefact.

    :param body: Parsed body dict (includes ``kind``).
    :param public_key_der: Issuer public key (SubjectPublicKeyInfo DER).
    :param signature: Raw Ed25519 signature bytes.
    :param canonical_body: JCS bytes that were signed.
    """

    body: Mapping[str, Any]
    public_key_der: bytes
    signature: bytes
    canonical_body: bytes

    @property
    def kind(self) -> str:
        return str(self.body["kind"])


def sign_artefact(body: Mapping[str, Any], private_key: Ed25519PrivateKey) -> dict[str, Any]:
    """Sign ``body`` and return the wire envelope dict.

    :param body: Artefact body; must contain ``kind`` in :data:`ALLOWED_KINDS`
        and must not contain floats.
    :param private_key: Ed25519 issuer private key.
    :return: Envelope ``{\"body\": ..., \"sig\": {\"alg\", \"key\", \"val\"}}``.
    :raises ArtefactError: If ``kind`` is missing/invalid.
    :raises FloatInSignedBodyError: If a float appears in ``body``.
    """
    kind = body.get("kind")
    if kind not in ALLOWED_KINDS:
        raise ArtefactError(
            f"body.kind must be one of {sorted(ALLOWED_KINDS)}, got {kind!r}"
        )
    body_dict = dict(body)
    canonical = jcs_dumps(body_dict)
    signature = private_key.sign(canonical)
    public_der = private_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return {
        "body": body_dict,
        "sig": {
            "alg": "Ed25519",
            "key": _b64url_encode(public_der),
            "val": _b64url_encode(signature),
        },
    }


def verify_artefact(envelope: Mapping[str, Any]) -> SignedArtefact:
    """Verify an envelope and return a :class:`SignedArtefact`.

    Verification runs **before** any body field is trusted.

    :param envelope: Wire envelope with ``body`` and ``sig``.
    :return: Verified artefact.
    :raises ArtefactVerificationError: On structural or cryptographic failure.
    :raises FloatInSignedBodyError: If the body contains a float.
    """
    try:
        body = envelope["body"]
        sig = envelope["sig"]
    except KeyError as exc:
        raise ArtefactVerificationError(f"missing envelope field: {exc}") from exc
    if not isinstance(body, dict) or not isinstance(sig, dict):
        raise ArtefactVerificationError("body and sig must be objects")
    if sig.get("alg") != "Ed25519":
        raise ArtefactVerificationError(f"unsupported alg: {sig.get('alg')!r}")
    kind = body.get("kind")
    if kind not in ALLOWED_KINDS:
        raise ArtefactVerificationError(f"invalid or missing body.kind: {kind!r}")
    try:
        public_der = _b64url_decode(str(sig["key"]))
        signature = _b64url_decode(str(sig["val"]))
    except Exception as exc:  # noqa: BLE001 — treat decode failures as verify failures
        raise ArtefactVerificationError("malformed sig.key or sig.val") from exc

    # Canonicalise before loading the key so float rejection happens first.
    canonical = jcs_dumps(body)

    try:
        loaded = load_der_public_key(public_der)
    except Exception as exc:  # noqa: BLE001
        raise ArtefactVerificationError("sig.key is not a valid DER public key") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise ArtefactVerificationError("sig.key is not an Ed25519 public key")

    try:
        loaded.verify(signature, canonical)
    except InvalidSignature as exc:
        raise ArtefactVerificationError("signature verification failed") from exc

    return SignedArtefact(
        body=dict(body),
        public_key_der=public_der,
        signature=signature,
        canonical_body=canonical,
    )


def generate_ed25519_private_key() -> Ed25519PrivateKey:
    """Generate a new Ed25519 private key (tests and local tooling)."""
    return Ed25519PrivateKey.generate()


__all__ = [
    "ALLOWED_KINDS",
    "ArtefactError",
    "ArtefactVerificationError",
    "FloatInSignedBodyError",
    "SignedArtefact",
    "generate_ed25519_private_key",
    "sign_artefact",
    "verify_artefact",
]
