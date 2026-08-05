# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Capability naming helpers.

Wire form::

    /cap/<issuer-digest>/<capability-path...>/<v=version>

* Component 0 is the fixed marker ``cap``.
* Component 1 is ``base32(sha256(issuer_pubkey_der))``, lowercase, unpadded.
* Components 2…n−1 are the capability path (one or more).
* Component n is the version, marked with the ``v=`` prefix.

Longest-prefix matching for the C-FIB excludes the version component — use
:func:`capability_lpm_prefix`. All construction and parsing of capability names
must go through this module; do not assemble ``/cap/...`` strings ad hoc.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from agentic.port.names import Name

CAP_MARKER: bytes = b"cap"
VERSION_MARKER: bytes = b"v="


class CapabilityNamingError(ValueError):
    """Raised when a capability name cannot be built or parsed."""


@dataclass(frozen=True, slots=True)
class ParsedCapabilityName:
    """Result of :func:`parse_capability_name`.

    :param issuer_digest: Lowercase unpadded base32 SHA-256 of the issuer key.
    :param path: Capability path components (never includes the version).
    :param version: Version string without the ``v=`` marker.
    """

    issuer_digest: bytes
    path: tuple[bytes, ...]
    version: str


def issuer_digest_from_key(issuer_pubkey_der: bytes) -> bytes:
    """Return the issuer name component for a DER-encoded public key.

    :param issuer_pubkey_der: SubjectPublicKeyInfo (or raw key bytes used as
        the signing identity). Digested as-is — callers must pass a stable
        encoding.
    :return: Lowercase unpadded base32 of SHA-256(``issuer_pubkey_der``).
    """
    digest = hashlib.sha256(issuer_pubkey_der).digest()
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return encoded.encode("ascii")


def _as_component(part: bytes | str) -> bytes:
    if isinstance(part, str):
        return part.encode("utf-8")
    return part


def capability_name(
    issuer_pubkey_der: bytes,
    path: Sequence[bytes | str],
    version: str,
) -> Name:
    """Build a capability ``Name`` bound to ``issuer_pubkey_der``.

    :param issuer_pubkey_der: Issuer public key bytes (stable DER encoding).
    :param path: One or more capability path components.
    :param version: Version string (without ``v=``); encoded as ``v=<version>``.
    :return: Hierarchical capability name.
    :raises CapabilityNamingError: If ``path`` is empty, a path component
        starts with ``v=``, or ``version`` is empty.
    """
    if not path:
        raise CapabilityNamingError("capability path must have at least one component")
    if not version:
        raise CapabilityNamingError("version must be non-empty")

    path_components: list[bytes] = []
    for part in path:
        component = _as_component(part)
        if component.startswith(VERSION_MARKER):
            raise CapabilityNamingError(
                f"capability path component must not start with {VERSION_MARKER!r}: "
                f"{component!r}"
            )
        if not component:
            raise CapabilityNamingError("capability path components must be non-empty")
        path_components.append(component)

    version_component = VERSION_MARKER + version.encode("utf-8")
    components = (
        CAP_MARKER,
        issuer_digest_from_key(issuer_pubkey_der),
        *path_components,
        version_component,
    )
    return Name(components)


def parse_capability_name(name: Name) -> ParsedCapabilityName:
    """Parse a capability ``Name`` into issuer digest, path, and version.

    :param name: Hierarchical name produced by :func:`capability_name` (or
        equivalent on the wire).
    :return: Structured parts.
    :raises CapabilityNamingError: If the name is not a well-formed capability
        name.
    """
    comps = name.components
    # cap + issuer + ≥1 path + version  => minimum length 4
    if len(comps) < 4:
        raise CapabilityNamingError(
            f"capability name too short ({len(comps)} components): {comps!r}"
        )
    if comps[0] != CAP_MARKER:
        raise CapabilityNamingError(
            f"capability name must start with {CAP_MARKER!r}, got {comps[0]!r}"
        )
    version_comp = comps[-1]
    if not version_comp.startswith(VERSION_MARKER):
        raise CapabilityNamingError(
            f"final component must be a marked version ({VERSION_MARKER!r}…), "
            f"got {version_comp!r}"
        )
    version = version_comp[len(VERSION_MARKER) :].decode("utf-8")
    if not version:
        raise CapabilityNamingError("version marker present but version string empty")
    path = comps[2:-1]
    if not path:
        raise CapabilityNamingError("capability path missing")
    for component in path:
        if component.startswith(VERSION_MARKER):
            raise CapabilityNamingError(
                f"path component must not start with {VERSION_MARKER!r}: {component!r}"
            )
    return ParsedCapabilityName(
        issuer_digest=comps[1],
        path=tuple(path),
        version=version,
    )


def capability_lpm_prefix(name: Name) -> Name:
    """Return the stage-1 LPM prefix (capability name with version stripped).

    :param name: Full capability name including the marked version component.
    :return: Prefix ``/cap/<issuer>/<path...>`` excluding version.
    :raises CapabilityNamingError: If ``name`` is not a well-formed capability
        name.
    """
    parsed = parse_capability_name(name)
    return Name((CAP_MARKER, parsed.issuer_digest, *parsed.path))


def issuer_digest_matches(name: Name, issuer_pubkey_der: bytes) -> bool:
    """Return True if ``name``'s issuer component equals the key's digest.

    Used at descriptor registration to reject forgeries whose signing key does
    not match the name. Matching is structural — no external lookup.

    :param name: Capability name to check.
    :param issuer_pubkey_der: Candidate issuer public key.
    :return: Whether the digests are equal.
    :raises CapabilityNamingError: If ``name`` is not a well-formed capability
        name.
    """
    parsed = parse_capability_name(name)
    return parsed.issuer_digest == issuer_digest_from_key(issuer_pubkey_der)
