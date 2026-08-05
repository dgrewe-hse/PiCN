# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Tests for binding registration conformance and descriptor-authoritative invoke."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.agentic_layer.naming import capability_name
from agentic.binding import (
    CapabilityRegistry,
    DeterministicBackend,
    SchemaCompatibilityError,
    SchemaProfileError,
    assert_restricted_profile,
    schemas_compatible,
)
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact


def _descriptor(*, input_schema: dict, output_schema: dict):
    key = generate_ed25519_private_key()
    der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    path = (b"hospital", b"beds")
    body = {
        "kind": "capability-descriptor",
        "domain": "hospital",
        "task": "beds",
        "version": "1.0.0",
        "constraints": {"jurisdiction": "DE"},
        "attestation_policy": "optional",
        "reputation_threshold": "0.5",
        "cost": "1",
        "input_schema": input_schema,
        "output_schema": output_schema,
        "freshness_bound_s": 60,
        "revocation_pointer": "none",
    }
    env = sign_artefact(body, key)
    return parse_descriptor_body(verify_artefact(env), capability_path=path)


BASE_IN = {
    "type": "object",
    "properties": {"patient_id": {"type": "string"}},
    "required": ["patient_id"],
    "additionalProperties": False,
}
BASE_OUT = {
    "type": "object",
    "properties": {"beds_free": {"type": "integer"}},
    "required": ["beds_free"],
    "additionalProperties": False,
}


def test_reject_unsupported_construct_at_registration() -> None:
    with pytest.raises(SchemaProfileError, match="oneOf"):
        assert_restricted_profile({"oneOf": [{"type": "string"}, {"type": "integer"}]})


def test_input_backend_must_accept_superset() -> None:
    """Backend requiring an extra field rejects valid descriptor traffic."""
    backend_in = {
        "type": "object",
        "properties": {
            "patient_id": {"type": "string"},
            "ward": {"type": "string"},
        },
        "required": ["patient_id", "ward"],
        "additionalProperties": False,
    }
    with pytest.raises(SchemaCompatibilityError, match="SUPERSET"):
        schemas_compatible(
            descriptor_input=BASE_IN,
            descriptor_output=BASE_OUT,
            backend_input=backend_in,
            backend_output=BASE_OUT,
        )


def test_output_backend_must_return_subset() -> None:
    """Backend advertising an undeclared output field is rejected."""
    backend_out = {
        "type": "object",
        "properties": {
            "beds_free": {"type": "integer"},
            "secret": {"type": "string"},
        },
        "required": ["beds_free"],
        "additionalProperties": False,
    }
    with pytest.raises(SchemaCompatibilityError, match="SUBSET"):
        schemas_compatible(
            descriptor_input=BASE_IN,
            descriptor_output=BASE_OUT,
            backend_input=BASE_IN,
            backend_output=backend_out,
        )


def test_compatible_when_backend_input_wider_output_equal() -> None:
    wider_in = {
        "type": "object",
        "properties": {
            "patient_id": {"type": "string"},
            "ward": {"type": "string"},
        },
        "required": ["patient_id"],
        "additionalProperties": False,
    }
    schemas_compatible(
        descriptor_input=BASE_IN,
        descriptor_output=BASE_OUT,
        backend_input=wider_in,
        backend_output=BASE_OUT,
    )


@pytest.mark.asyncio
async def test_invoke_validates_descriptor_not_backend() -> None:
    desc = _descriptor(input_schema=BASE_IN, output_schema=BASE_OUT)
    wider_in = {
        "type": "object",
        "properties": {
            "patient_id": {"type": "string"},
            "ward": {"type": "string"},
        },
        "required": ["patient_id"],
        "additionalProperties": False,
    }

    async def handler(payload: dict) -> dict:
        return {"beds_free": 3}

    backend = DeterministicBackend(
        handler, input_schema=wider_in, output_schema=BASE_OUT
    )
    registry = CapabilityRegistry()
    registry.register(desc, backend, backend_label="deterministic")

    ok = await registry.invoke(
        desc.name, {"patient_id": "p1"}, deadline=1.0, quote_id="q-1"
    )
    assert ok.input_rejected is False
    assert ok.output_valid is True
    assert ok.payload == {"beds_free": 3}
    assert ok.quote_id == "q-1"

    rejected = await registry.invoke(desc.name, {}, deadline=1.0)
    assert rejected.input_rejected is True
    assert rejected.payload is None


@pytest.mark.asyncio
async def test_output_violation_sets_flag_not_raise() -> None:
    desc = _descriptor(input_schema=BASE_IN, output_schema=BASE_OUT)

    async def bad_handler(payload: dict) -> dict:
        return {"beds_free": "many"}

    backend = DeterministicBackend(
        bad_handler, input_schema=BASE_IN, output_schema=BASE_OUT
    )
    registry = CapabilityRegistry()
    registry.register(desc, backend)
    result = await registry.invoke(desc.name, {"patient_id": "p1"}, deadline=1.0)
    assert result.input_rejected is False
    assert result.output_valid is False
    assert result.payload == {"beds_free": "many"}


@pytest.mark.asyncio
async def test_invocation_path_identical_across_backends() -> None:
    desc = _descriptor(input_schema=BASE_IN, output_schema=BASE_OUT)
    registry = CapabilityRegistry()

    async def a(payload: dict) -> dict:
        return {"beds_free": 1}

    async def b(payload: dict) -> dict:
        return {"beds_free": 2}

    registry.register(
        desc,
        DeterministicBackend(a, input_schema=BASE_IN, output_schema=BASE_OUT),
        backend_label="a",
    )
    r1 = await registry.invoke(desc.name, {"patient_id": "x"}, deadline=0.0)
    registry.register(
        desc,
        DeterministicBackend(b, input_schema=BASE_IN, output_schema=BASE_OUT),
        backend_label="b",
    )
    r2 = await registry.invoke(desc.name, {"patient_id": "x"}, deadline=0.0)
    assert r1.payload != r2.payload
    assert r1.output_valid and r2.output_valid


def test_scenario_registry_is_pluggable() -> None:
    from agentic.scenario import available_scenarios, register_scenario

    @register_scenario
    class _Toy:
        scenario_name = "toy-scenario"

    assert "toy-scenario" in available_scenarios()


def test_validate_runtime_payloads() -> None:
    from agentic.binding.schema import validate_against_schema
    from agentic.port.errors import DescriptorInvalid

    validate_against_schema({"patient_id": "p"}, BASE_IN)
    with pytest.raises(DescriptorInvalid):
        validate_against_schema({"patient_id": 1}, BASE_IN)
    with pytest.raises(DescriptorInvalid):
        validate_against_schema({"beds_free": 1, "extra": True}, BASE_OUT)

    validate_against_schema({"beds_free": 2}, BASE_OUT)
    with pytest.raises(DescriptorInvalid):
        validate_against_schema([1, 2], {"type": "array", "items": {"type": "string"}})
    validate_against_schema(
        ["a", "b"], {"type": "array", "items": {"type": "string"}}
    )
    with pytest.raises(SchemaProfileError, match="type=number"):
        assert_restricted_profile({"type": "number"})
    with pytest.raises(SchemaProfileError, match=r"\$ref"):
        assert_restricted_profile({"$ref": "https://example.com/schema.json"})


@pytest.mark.asyncio
async def test_sync_handler_and_unknown_registration() -> None:
    desc = _descriptor(input_schema=BASE_IN, output_schema=BASE_OUT)

    def sync_handler(payload: dict) -> dict:
        return {"beds_free": 9}

    backend = DeterministicBackend(
        sync_handler, input_schema=BASE_IN, output_schema=BASE_OUT
    )
    registry = CapabilityRegistry()
    registry.register(desc, backend)
    result = await registry.invoke(desc.name, {"patient_id": "z"}, deadline=0.0)
    assert result.payload == {"beds_free": 9}
    assert registry.get(desc.name) is not None
    from agentic.port.names import Name
    from agentic.port.errors import DescriptorInvalid

    with pytest.raises(DescriptorInvalid, match="no backend"):
        await registry.invoke(Name((b"missing",)), {"patient_id": "z"}, deadline=0.0)


def test_register_wraps_profile_errors() -> None:
    from agentic.port.errors import DescriptorInvalid

    desc = _descriptor(input_schema=BASE_IN, output_schema=BASE_OUT)
    bad = DeterministicBackend(
        lambda p: p,
        input_schema={"oneOf": [{"type": "string"}]},
        output_schema=BASE_OUT,
    )
    registry = CapabilityRegistry()
    with pytest.raises(DescriptorInvalid, match="oneOf"):
        registry.register(desc, bad)


def test_schema_profile_edge_cases() -> None:
    from agentic.binding.schema import validate_against_schema
    from agentic.port.errors import DescriptorInvalid

    with pytest.raises(SchemaProfileError):
        assert_restricted_profile("x")  # type: ignore[arg-type]
    with pytest.raises(SchemaProfileError, match="additionalProperties"):
        assert_restricted_profile(
            {"type": "object", "additionalProperties": {"type": "string"}}
        )
    with pytest.raises(SchemaProfileError, match="required"):
        assert_restricted_profile({"type": "object", "required": [1]})  # type: ignore[list-item]
    defs_ok = {
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/S"}},
        "$defs": {"S": {"type": "string"}},
        "additionalProperties": False,
    }
    assert_restricted_profile(defs_ok)
    with pytest.raises(DescriptorInvalid, match=r"\$ref"):
        validate_against_schema({"x": "a"}, defs_ok)
    with pytest.raises(DescriptorInvalid):
        validate_against_schema("nope", {"type": "integer"})
    with pytest.raises(DescriptorInvalid):
        validate_against_schema(1, {"type": "boolean"})
    with pytest.raises(DescriptorInvalid):
        validate_against_schema(0, {"type": "null"})
    with pytest.raises(DescriptorInvalid):
        validate_against_schema({}, {"type": "array", "items": {"type": "string"}})
    # enum narrowing
    schemas_compatible(
        descriptor_input={"type": "string", "enum": ["a"]},
        descriptor_output=BASE_OUT,
        backend_input={"type": "string", "enum": ["a", "b"]},
        backend_output=BASE_OUT,
    )
    with pytest.raises(SchemaCompatibilityError):
        schemas_compatible(
            descriptor_input={"type": "string", "enum": ["a", "b"]},
            descriptor_output=BASE_OUT,
            backend_input={"type": "string", "enum": ["a"]},
            backend_output=BASE_OUT,
        )
