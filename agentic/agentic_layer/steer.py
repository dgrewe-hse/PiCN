# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""AgenticSteer — mid-flight sub-intent mutation.

A distinct agentic-layer message (not a keepalive reuse). A Steer may change
only ``payload`` and ``constraints.latency_bound``. Attempts to touch the
expected sub-intent set, aggregation policy, capability name, or credential are
rejected wholesale. The steer-chain / Merkle leaf update is a Phase D seam.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

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

# PROVISIONAL: bound pathological continuous steering; derive from measurement.
MAX_STEERS_PER_SUBINTENT: int = 8

# Domain-separated sentinel for "never steered" (Phase D folds this into leaves).
NULL_STEER: bytes = b"\x00" * 32

_ALLOWED_TOP_KEYS = frozenset({"payload", "constraints"})
_ALLOWED_CONSTRAINT_KEYS = frozenset({"latency_bound"})
_FORBIDDEN_MUTATION_KEYS = frozenset(
    {
        "expected_subintent_set",
        "aggregation_policy",
        "capability",
        "capability_name",
        "credential",
        "name",
    }
)


class SteerRejected(ValueError):
    """Raised when an AgenticSteer cannot be applied (wholesale rejection)."""


@dataclass
class InFlightLeaf:
    """Mutable runtime state for one expected sub-intent leaf.

    :param payload: Current request payload (may be steered).
    :param latency_bound: Current latency bound (may be steered).
    :param capability: Immutable capability path for this leaf.
    :param credential: Immutable credential blob for this leaf.
    :param steer_count: Number of Steers already applied.
    :param steer_chain_head: Hash-chain head; Phase D folds this into the Merkle leaf.
    """

    payload: bytes
    latency_bound: int | None
    capability: tuple[str, ...]
    credential: bytes
    steer_count: int = 0
    steer_chain_head: bytes = NULL_STEER


@dataclass
class SteerTargetEntry:
    """Minimal Context PIT entry surface needed to apply Steers.

    Phase D replaces / extends this with the full Context PIT. Steer must never
    create entries — only mutate known, non-terminated ones.

    :param parent_intent_digest: Entry key.
    :param issuer_public_key_der: Original intent issuer (Steer authorisation).
    :param aggregation_policy: Immutable under Steer.
    :param expected_subintent_set: Immutable under Steer (canonical leaf ids).
    :param leaves: Per-leaf mutable runtime state (payload / latency only).
    :param terminated: When True, Steers are refused.
    """

    parent_intent_digest: bytes
    issuer_public_key_der: bytes
    aggregation_policy: str
    expected_subintent_set: tuple[str, ...]
    leaves: list[InFlightLeaf]
    terminated: bool = False


@dataclass
class SteerStore:
    """In-memory store of Steer targets (stand-in until Context PIT lands)."""

    _entries: MutableMapping[bytes, SteerTargetEntry] = field(default_factory=dict)

    def get(self, parent_intent_digest: bytes) -> SteerTargetEntry | None:
        return self._entries.get(parent_intent_digest)

    def put(self, entry: SteerTargetEntry) -> None:
        """Register an entry created by the intent path — never by a Steer."""
        self._entries[entry.parent_intent_digest] = entry


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def append_steer_chain(previous_head: bytes, steer_canonical: bytes) -> bytes:
    """Extend the steer hash chain (seam used by Phase D leaf hashing).

    :param previous_head: Prior chain head (``NULL_STEER`` if never steered).
    :param steer_canonical: Canonical bytes of the applied Steer body.
    :return: New chain head ``SHA-256(prev || SHA-256(steer))``.
    """
    steer_digest = hashlib.sha256(steer_canonical).digest()
    return hashlib.sha256(previous_head + steer_digest).digest()


def _validate_mutations(mutations: Mapping[str, Any]) -> None:
    if not isinstance(mutations, dict) or not mutations:
        raise SteerRejected("mutations must be a non-empty object")
    keys = set(mutations.keys())
    if keys & _FORBIDDEN_MUTATION_KEYS:
        raise SteerRejected(
            "Steer must not mutate immutable fields: "
            f"{sorted(keys & _FORBIDDEN_MUTATION_KEYS)}"
        )
    unknown = keys - _ALLOWED_TOP_KEYS
    if unknown:
        raise SteerRejected(
            f"unknown mutation fields (allow-list only): {sorted(unknown)}"
        )
    if "payload" in mutations and not isinstance(mutations["payload"], (bytes, str)):
        raise SteerRejected("mutations.payload must be bytes or str")
    if "constraints" in mutations:
        constraints = mutations["constraints"]
        if not isinstance(constraints, dict):
            raise SteerRejected("mutations.constraints must be an object")
        c_unknown = set(constraints.keys()) - _ALLOWED_CONSTRAINT_KEYS
        if c_unknown:
            raise SteerRejected(
                f"unknown constraint mutations (allow-list only): {sorted(c_unknown)}"
            )
        if "latency_bound" not in constraints:
            raise SteerRejected("constraints must include latency_bound")
        lb = constraints["latency_bound"]
        if type(lb) is not int or isinstance(lb, bool) or lb < 0:
            raise SteerRejected("constraints.latency_bound must be a non-negative int")


def build_steer_body(
    *,
    parent_intent_digest: bytes,
    leaf_index: int,
    mutations: Mapping[str, Any],
    issued_at_ms: int,
) -> dict[str, Any]:
    """Build a Steer body dict ready for signing (no floats).

    :param parent_intent_digest: Target Context PIT entry digest.
    :param leaf_index: Canonical leaf index.
    :param mutations: Allow-listed mutations only.
    :param issued_at_ms: Issue time as integer milliseconds.
    :return: Body including ``kind=agentic-steer``.
    """
    _validate_mutations(mutations)
    if leaf_index < 0:
        raise SteerRejected("leaf_index must be non-negative")
    if type(issued_at_ms) is not int or isinstance(issued_at_ms, bool) or issued_at_ms < 0:
        raise SteerRejected("issued_at_ms must be a non-negative int")

    wire_mutations: dict[str, Any] = {}
    if "payload" in mutations:
        payload = mutations["payload"]
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = payload
        wire_mutations["payload"] = _b64url_encode(payload_bytes)
    if "constraints" in mutations:
        wire_mutations["constraints"] = dict(mutations["constraints"])

    return {
        "kind": "agentic-steer",
        "parent_intent_digest": _b64url_encode(parent_intent_digest),
        "leaf_index": leaf_index,
        "mutations": wire_mutations,
        "issued_at_ms": issued_at_ms,
    }


def sign_steer(
    body: Mapping[str, Any],
    issuer_private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    """Sign a Steer body with the **original intent issuer** key.

    :param body: Steer body from :func:`build_steer_body`.
    :param issuer_private_key: Original intent issuer's key.
    :return: Detached-signature envelope.
    """
    if body.get("kind") != "agentic-steer":
        raise SteerRejected("body.kind must be agentic-steer")
    body_dict = dict(body)
    try:
        canonical = jcs_dumps(body_dict)
    except FloatInSignedBodyError as exc:
        raise SteerRejected(str(exc)) from exc
    signature = issuer_private_key.sign(canonical)
    public_der = issuer_private_key.public_key().public_bytes(
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


def apply_steer(
    store: SteerStore,
    envelope: Mapping[str, Any],
) -> SteerTargetEntry:
    """Verify and apply an AgenticSteer to a known, non-terminated entry.

    :param store: Entry store (does not create entries).
    :param envelope: Signed Steer envelope.
    :return: Updated entry.
    :raises SteerRejected: On any validation / authorisation / state failure.
    """
    body = envelope.get("body")
    sig = envelope.get("sig")
    if not isinstance(body, dict) or not isinstance(sig, dict):
        raise SteerRejected("malformed Steer envelope")
    if body.get("kind") != "agentic-steer":
        raise SteerRejected("not an agentic-steer")

    try:
        canonical = jcs_dumps(body)
    except FloatInSignedBodyError as exc:
        raise SteerRejected(str(exc)) from exc

    try:
        public_der = _b64url_decode(str(sig["key"]))
        signature = _b64url_decode(str(sig["val"]))
        if sig.get("alg") != "Ed25519":
            raise SteerRejected("unsupported Steer alg")
        loaded = load_der_public_key(public_der)
        if not isinstance(loaded, Ed25519PublicKey):
            raise SteerRejected("Steer key is not Ed25519")
        loaded.verify(signature, canonical)
    except SteerRejected:
        raise
    except InvalidSignature as exc:
        raise SteerRejected("Steer signature verification failed") from exc
    except Exception as exc:  # noqa: BLE001
        raise SteerRejected(f"Steer signature verification failed: {exc}") from exc

    try:
        parent = _b64url_decode(str(body["parent_intent_digest"]))
        leaf_index = body["leaf_index"]
        mutations = body["mutations"]
    except Exception as exc:  # noqa: BLE001
        raise SteerRejected("Steer body missing required fields") from exc
    if type(leaf_index) is not int or isinstance(leaf_index, bool):
        raise SteerRejected("leaf_index must be an int")
    if not isinstance(mutations, dict) or not mutations:
        raise SteerRejected("mutations must be a non-empty object")

    decoded: dict[str, Any] = {}
    if "payload" in mutations:
        decoded["payload"] = _b64url_decode(str(mutations["payload"]))
    if "constraints" in mutations:
        decoded["constraints"] = mutations["constraints"]
    _validate_mutations(decoded)

    entry = store.get(parent)
    if entry is None:
        raise SteerRejected("unknown parent_intent_digest — Steer creates nothing")
    if entry.terminated:
        raise SteerRejected("Steer refused for terminated entry")
    if public_der != entry.issuer_public_key_der:
        raise SteerRejected("Steer must be signed by the original intent issuer")
    if leaf_index < 0 or leaf_index >= len(entry.leaves):
        raise SteerRejected("leaf_index out of range")

    leaf = entry.leaves[leaf_index]
    if leaf.steer_count >= MAX_STEERS_PER_SUBINTENT:
        raise SteerRejected("MAX_STEERS_PER_SUBINTENT exceeded")

    if "payload" in decoded:
        leaf.payload = decoded["payload"]
    if "constraints" in decoded:
        leaf.latency_bound = decoded["constraints"]["latency_bound"]

    leaf.steer_chain_head = append_steer_chain(leaf.steer_chain_head, canonical)
    leaf.steer_count += 1
    return entry


# Public type name for the signed envelope (body.kind == "agentic-steer").
AgenticSteer = dict[str, Any]

__all__ = [
    "MAX_STEERS_PER_SUBINTENT",
    "NULL_STEER",
    "AgenticSteer",
    "InFlightLeaf",
    "SteerRejected",
    "SteerStore",
    "SteerTargetEntry",
    "append_steer_chain",
    "apply_steer",
    "build_steer_body",
    "sign_steer",
]
