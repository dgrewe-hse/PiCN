# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Append-only hash-chained accountability log (payload digests only).

Each entry is an A-004 signed artefact. ``prev_hash`` covers the JCS canonical
bytes of the previous entry's body. Entry ``type`` is a closed set.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentic.trust.jcs import jcs_dumps
from agentic.trust.signed_artefact import (
    ArtefactVerificationError,
    sign_artefact,
    verify_artefact,
)

ENTRY_TYPES = frozenset(
    {
        "quote_issued",
        "claim_recorded",
        "trace_root_anchored",
        "verification_outcome",
        "reputation_updated",
    }
)

_ALLOWED_ENTRY_BODY_KEYS = frozenset(
    {
        "kind",
        "seq",
        "prev_hash",
        "at_ms",
        "type",
        "subject",
        "payload_digest",
    }
)

_GENESIS_PREV = hashlib.sha256(b"agentic/accountability-log/genesis/v1").digest()


class LogError(ValueError):
    """Raised when the accountability log cannot append or verify."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


@dataclass
class AccountabilityLog:
    """In-memory (optionally file-backed) hash-chained log of digests.

    :param node_private_key: Node key used to sign entries.
    :param path: Optional JSON-lines path for persistence.
    """

    node_private_key: Ed25519PrivateKey
    path: Path | None = None
    _entries: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _prev_canonical: bytes | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.path is not None and self.path.is_file():
            self._load(self.path)

    @property
    def length(self) -> int:
        return len(self._entries)

    def append(
        self,
        *,
        entry_type: str,
        subject: str,
        payload_digest: bytes,
        at_ms: int,
    ) -> dict[str, Any]:
        """Append a signed log entry storing ``payload_digest`` only.

        :param entry_type: One of :data:`ENTRY_TYPES`.
        :param subject: Intent digest / agent id / quote id (string form).
        :param payload_digest: SHA-256 of the referenced object (32 bytes).
        :param at_ms: Event time (injected clock, ms).
        :return: The signed envelope that was appended.
        """
        if entry_type not in ENTRY_TYPES:
            raise LogError(f"unknown entry type: {entry_type!r}")
        if len(payload_digest) != 32:
            raise LogError("payload_digest must be 32 bytes")
        if type(at_ms) is not int or isinstance(at_ms, bool) or at_ms < 0:
            raise LogError("at_ms must be a non-negative int")

        if self._prev_canonical is None:
            prev_hash = _GENESIS_PREV
        else:
            prev_hash = hashlib.sha256(self._prev_canonical).digest()

        body: dict[str, Any] = {
            "kind": "accountability-log-entry",
            "seq": len(self._entries),
            "prev_hash": _b64url_encode(prev_hash),
            "at_ms": at_ms,
            "type": entry_type,
            "subject": subject,
            "payload_digest": _b64url_encode(payload_digest),
        }
        envelope = sign_artefact(body, self.node_private_key)
        canonical = jcs_dumps(body)
        self._entries.append(envelope)
        self._prev_canonical = canonical
        if self.path is not None:
            self._append_line(self.path, envelope)
        return envelope

    def verify_chain(self) -> None:
        """Re-walk the chain; raise :class:`LogError` on the first break."""
        prev_canonical: bytes | None = None
        for index, envelope in enumerate(self._entries):
            try:
                artefact = verify_artefact(envelope)
            except (ArtefactVerificationError, Exception) as exc:
                raise LogError(f"entry {index}: signature failed: {exc}") from exc
            if artefact.kind != "accountability-log-entry":
                raise LogError(f"entry {index}: wrong kind")
            body = dict(artefact.body)
            if body.get("seq") != index:
                raise LogError(f"entry {index}: seq mismatch")
            if body.get("type") not in ENTRY_TYPES:
                raise LogError(f"entry {index}: unknown type")
            unknown_keys = set(body) - _ALLOWED_ENTRY_BODY_KEYS
            if unknown_keys:
                raise LogError(
                    f"entry {index}: forbidden or unknown fields: {sorted(unknown_keys)}"
                )
            expected_prev = (
                _GENESIS_PREV
                if prev_canonical is None
                else hashlib.sha256(prev_canonical).digest()
            )
            try:
                got_prev = _b64url_decode(str(body["prev_hash"]))
            except Exception as exc:  # noqa: BLE001
                raise LogError(f"entry {index}: bad prev_hash") from exc
            if got_prev != expected_prev:
                raise LogError(f"entry {index}: prev_hash chain broken")
            prev_canonical = jcs_dumps(body)

    def entries(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._entries)

    def _append_line(self, path: Path, envelope: Mapping[str, Any]) -> None:
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, separators=(",", ":"), sort_keys=True))
            handle.write("\n")

    def _load(self, path: Path) -> None:
        import json

        self._entries.clear()
        self._prev_canonical = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            envelope = json.loads(line)
            body = envelope["body"]
            canonical = jcs_dumps(body)
            self._entries.append(envelope)
            self._prev_canonical = canonical
        self.verify_chain()


__all__ = [
    "ENTRY_TYPES",
    "AccountabilityLog",
    "LogError",
]
