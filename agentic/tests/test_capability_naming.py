# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Unit tests for capability naming helpers."""

from __future__ import annotations

import hashlib

import pytest

from agentic.agentic_layer.naming import (
    CAP_MARKER,
    VERSION_MARKER,
    CapabilityNamingError,
    capability_lpm_prefix,
    capability_name,
    issuer_digest_from_key,
    issuer_digest_matches,
    parse_capability_name,
)
from agentic.port.names import Name


def _key(seed: bytes = b"issuer-1") -> bytes:
    # Stable stand-in for a DER SubjectPublicKeyInfo.
    return hashlib.sha256(seed).digest()


@pytest.mark.parametrize("path_len", [1, 2, 5])
def test_capability_name_round_trip(path_len: int) -> None:
    key = _key()
    path = [f"seg{i}".encode("utf-8") for i in range(path_len)]
    name = capability_name(key, path, version="1.2.0")
    parsed = parse_capability_name(name)
    assert parsed.issuer_digest == issuer_digest_from_key(key)
    assert parsed.path == tuple(path)
    assert parsed.version == "1.2.0"
    assert name.components[0] == CAP_MARKER
    assert name.components[-1] == VERSION_MARKER + b"1.2.0"


def test_version_extraction_independent_of_path_length() -> None:
    key = _key()
    for path_len in (1, 2, 5):
        path = [f"p{i}".encode() for i in range(path_len)]
        name = capability_name(key, path, "9.0.0")
        assert parse_capability_name(name).version == "9.0.0"


def test_lpm_prefix_excludes_version() -> None:
    key = _key()
    name = capability_name(key, [b"hospital", b"beds"], "1.0.0")
    prefix = capability_lpm_prefix(name)
    assert prefix.components == name.components[:-1]
    assert not prefix.components[-1].startswith(VERSION_MARKER)


def test_path_component_starting_with_version_marker_rejected() -> None:
    with pytest.raises(CapabilityNamingError, match="must not start"):
        capability_name(_key(), [b"ok", b"v=sneaky"], "1.0.0")


def test_empty_path_and_version_rejected() -> None:
    with pytest.raises(CapabilityNamingError, match="at least one"):
        capability_name(_key(), [], "1.0.0")
    with pytest.raises(CapabilityNamingError, match="non-empty"):
        capability_name(_key(), [b"svc"], "")


def test_issuer_digest_matches_and_rejects_other_key() -> None:
    key = _key(b"a")
    other = _key(b"b")
    name = capability_name(key, [b"svc"], "1.0.0")
    assert issuer_digest_matches(name, key) is True
    assert issuer_digest_matches(name, other) is False


def test_parse_rejects_non_capability_names() -> None:
    with pytest.raises(CapabilityNamingError):
        parse_capability_name(Name((b"notcap", b"x", b"y", b"v=1")))
    with pytest.raises(CapabilityNamingError):
        parse_capability_name(Name((CAP_MARKER, b"iss", b"path")))  # no version
    with pytest.raises(CapabilityNamingError):
        parse_capability_name(Name((CAP_MARKER, b"iss", b"path", b"1.0.0")))  # unmarked


def test_str_path_components_accepted() -> None:
    name = capability_name(_key(), ["hospital", "beds"], "1.0.0")
    assert parse_capability_name(name).path == (b"hospital", b"beds")


def test_empty_path_component_rejected() -> None:
    with pytest.raises(CapabilityNamingError, match="non-empty"):
        capability_name(_key(), [b"ok", b""], "1.0.0")


def test_parse_rejects_empty_version_and_v_equals_in_path() -> None:
    issuer = issuer_digest_from_key(_key())
    with pytest.raises(CapabilityNamingError, match="empty"):
        parse_capability_name(Name((CAP_MARKER, issuer, b"svc", VERSION_MARKER)))
    with pytest.raises(CapabilityNamingError, match="must not start"):
        parse_capability_name(
            Name((CAP_MARKER, issuer, VERSION_MARKER + b"bad", VERSION_MARKER + b"1"))
        )
