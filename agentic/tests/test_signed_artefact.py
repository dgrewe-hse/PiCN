# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests for JCS canonicalisation and the signed artefact envelope."""

from __future__ import annotations

import copy

import pytest

from agentic.trust import (
    ArtefactError,
    ArtefactVerificationError,
    FloatInSignedBodyError,
    generate_ed25519_private_key,
    jcs_dumps,
    sign_artefact,
    verify_artefact,
)


def _body(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "capability-descriptor",
        "name": "hospital-capacity",
        "version": "1.0.0",
    }
    base.update(extra)
    return base


def test_jcs_key_order_independent() -> None:
    a = {"z": 1, "a": {"y": 2, "b": 3}, "kind": "capability-descriptor"}
    b = {"kind": "capability-descriptor", "a": {"b": 3, "y": 2}, "z": 1}
    assert jcs_dumps(a) == jcs_dumps(b)


def test_jcs_rejects_float() -> None:
    with pytest.raises(FloatInSignedBodyError):
        jcs_dumps({"kind": "capability-descriptor", "cost": 0.5})


def test_sign_verify_round_trip_and_key_reorder() -> None:
    key = generate_ed25519_private_key()
    body_a = _body(z=1, a=True)
    body_b = {"a": True, "kind": "capability-descriptor", "name": "hospital-capacity", "version": "1.0.0", "z": 1}
    env_a = sign_artefact(body_a, key)
    env_b = sign_artefact(body_b, key)
    assert env_a["sig"]["val"] == env_b["sig"]["val"]
    verified = verify_artefact(env_a)
    assert verified.kind == "capability-descriptor"
    assert verified.body["name"] == "hospital-capacity"


def test_tampered_body_fails_verification() -> None:
    key = generate_ed25519_private_key()
    envelope = sign_artefact(_body(), key)
    tampered = copy.deepcopy(envelope)
    tampered["body"]["name"] = "evil"
    with pytest.raises(ArtefactVerificationError, match="signature"):
        verify_artefact(tampered)


def test_kind_substitution_fails_verification() -> None:
    key = generate_ed25519_private_key()
    envelope = sign_artefact(_body(), key)
    # Re-sign would succeed; forge by swapping kind without resigning.
    forged = copy.deepcopy(envelope)
    forged["body"]["kind"] = "task-graph-template"
    with pytest.raises(ArtefactVerificationError):
        verify_artefact(forged)


def test_float_rejected_at_sign_time() -> None:
    key = generate_ed25519_private_key()
    with pytest.raises(FloatInSignedBodyError):
        sign_artefact(_body(score=0.85), key)


def test_invalid_kind_rejected_at_sign_time() -> None:
    key = generate_ed25519_private_key()
    with pytest.raises(ArtefactError, match="kind"):
        sign_artefact({"kind": "other"}, key)


def test_jcs_escapes_and_controls() -> None:
    raw = {"kind": "capability-descriptor", "s": 'a"b\\c\n\t\r\b\f\x01'}
    out = jcs_dumps(raw).decode("utf-8")
    assert '\\"' in out
    assert "\\\\" in out
    assert "\\n" in out
    assert "\\u0001" in out


def test_jcs_bool_null_array() -> None:
    assert jcs_dumps([True, False, None, 0]) == b"[true,false,null,0]"


def test_jcs_rejects_unsupported_type() -> None:
    with pytest.raises(TypeError):
        jcs_dumps({"kind": "capability-descriptor", "x": object()})


def test_verify_rejects_bad_alg_and_structure() -> None:
    key = generate_ed25519_private_key()
    envelope = sign_artefact(_body(), key)
    bad_alg = copy.deepcopy(envelope)
    bad_alg["sig"]["alg"] = "RSA"
    with pytest.raises(ArtefactVerificationError, match="alg"):
        verify_artefact(bad_alg)
    with pytest.raises(ArtefactVerificationError):
        verify_artefact({"body": "x", "sig": {}})
    broken = copy.deepcopy(envelope)
    broken["sig"]["key"] = "!!!"
    with pytest.raises(ArtefactVerificationError):
        verify_artefact(broken)
