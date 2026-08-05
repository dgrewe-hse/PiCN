# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests for capability descriptor registration."""

from __future__ import annotations

import copy

import pytest

from agentic.agentic_layer.descriptor import register_descriptor
from agentic.agentic_layer.naming import capability_name, issuer_digest_from_key
from agentic.port.errors import DescriptorInvalid
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key, sign_artefact
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _body(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "capability-descriptor",
        "domain": "hospital-capacity",
        "task": "bed-availability-query",
        "version": "1.0.0",
        "constraints": {"jurisdiction": "DE-BW"},
        "attestation_policy": "required",
        "reputation_threshold": "0.50",
        "cost": "10",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "freshness_bound_s": 3600,
        "revocation_pointer": "/revocation/hospital-capacity",
    }
    base.update(extra)
    return base


def test_valid_descriptor_registers() -> None:
    key = generate_ed25519_private_key()
    envelope = sign_artefact(_body(), key)
    path = (b"hospital-capacity", b"bed-availability-query")
    desc = register_descriptor(envelope, capability_path=path)
    assert desc.domain == "hospital-capacity"
    assert desc.version == "1.0.0"
    assert desc.name == capability_name(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo),
        path,
        "1.0.0",
    )


def test_issuer_digest_mismatch_rejected() -> None:
    key = generate_ed25519_private_key()
    other = generate_ed25519_private_key()
    envelope = sign_artefact(_body(), key)
    path = (b"hospital-capacity",)
    # expected_name built under a different key → hard reject
    wrong_name = capability_name(
        other.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo),
        path,
        "1.0.0",
    )
    with pytest.raises(DescriptorInvalid, match="does not match"):
        register_descriptor(envelope, capability_path=path, expected_name=wrong_name)


def test_v_equals_path_component_rejected() -> None:
    key = generate_ed25519_private_key()
    envelope = sign_artefact(_body(), key)
    with pytest.raises(DescriptorInvalid, match="must not start"):
        register_descriptor(
            envelope, capability_path=(b"ok", b"v=sneaky")
        )


def test_tampered_envelope_rejected() -> None:
    key = generate_ed25519_private_key()
    envelope = sign_artefact(_body(), key)
    bad = copy.deepcopy(envelope)
    bad["body"]["domain"] = "evil"
    with pytest.raises(DescriptorInvalid, match="verification"):
        register_descriptor(bad, capability_path=(b"hospital-capacity",))


def test_missing_field_rejected() -> None:
    key = generate_ed25519_private_key()
    body = _body()
    del body["cost"]
    envelope = sign_artefact(body, key)
    with pytest.raises(DescriptorInvalid, match="cost"):
        register_descriptor(envelope, capability_path=(b"hospital-capacity",))
