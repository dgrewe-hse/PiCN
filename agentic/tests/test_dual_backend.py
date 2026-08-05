# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Dual-backend interchangeability: deterministic and Pydantic AI, same path."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict

pytest.importorskip("pydantic_ai")

from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.binding import CapabilityRegistry, DeterministicBackend
from agentic.binding.pydantic_ai_backend import PydanticAIBackend, load_model_preference
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact


class HospitalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str


class HospitalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    beds_free: int


INPUT_SCHEMA = {
    "type": "object",
    "properties": {"patient_id": {"type": "string"}},
    "required": ["patient_id"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"beds_free": {"type": "integer"}},
    "required": ["beds_free"],
    "additionalProperties": False,
}


def _descriptor():
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
        "input_schema": INPUT_SCHEMA,
        "output_schema": OUTPUT_SCHEMA,
        "freshness_bound_s": 60,
        "revocation_pointer": "none",
    }
    return parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)), capability_path=path
    )


@pytest.mark.asyncio
async def test_dual_backend_identical_invoke_path(tmp_path: Path) -> None:
    """Same descriptor, identical registry.invoke path, two backend kinds."""
    desc = _descriptor()
    registry = CapabilityRegistry()
    payload = {"patient_id": "p-42"}

    async def det(payload: dict) -> dict:
        return {"beds_free": 7}

    det_backend = DeterministicBackend(
        det, input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA
    )
    registry.register(desc, det_backend, backend_label="deterministic")
    r_det = await registry.invoke(desc.name, payload, deadline=1.0, quote_id="q-det")
    assert r_det.output_valid and r_det.payload == {"beds_free": 7}
    assert r_det.quote_id == "q-det"

    config = tmp_path / "model.toml"
    config.write_text('[model]\npreference = ["test"]\n', encoding="utf-8")
    assert load_model_preference(config) == ["test"]

    ai_backend = PydanticAIBackend(
        input_model=HospitalIn,
        output_model=HospitalOut,
        model_config_path=config,
        test_output={"beds_free": 7},
    )
    # Re-register against the SAME descriptor — fabric path unchanged.
    registry.register(desc, ai_backend, backend_label="pydantic-ai")
    r_ai = await registry.invoke(desc.name, payload, deadline=1.0, quote_id="q-ai")
    assert r_ai.output_valid
    assert r_ai.payload == {"beds_free": 7}
    assert r_ai.quote_id == "q-ai"
    # No fabric-side change: both results are BindingResponse from one method.
    assert type(r_det) is type(r_ai)


def test_model_config_missing_raises(tmp_path: Path) -> None:
    from agentic.binding.pydantic_ai_backend import ModelConfigError

    with pytest.raises(ModelConfigError):
        load_model_preference(tmp_path / "absent.toml")


def test_model_config_defaults_and_validation(tmp_path: Path) -> None:
    from agentic.binding.pydantic_ai_backend import ModelConfigError

    assert load_model_preference(None) == ["test"]

    bad_table = tmp_path / "no-model.toml"
    bad_table.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ModelConfigError, match=r"\[model\]"):
        load_model_preference(bad_table)

    bad_pref = tmp_path / "bad-pref.toml"
    bad_pref.write_text('[model]\npreference = []\n', encoding="utf-8")
    with pytest.raises(ModelConfigError, match="preference"):
        load_model_preference(bad_pref)


def test_schema_from_model_strips_and_nests() -> None:
    from agentic.binding.pydantic_ai_backend import schema_from_model

    class Nested(BaseModel):
        model_config = ConfigDict(extra="forbid")
        tags: list[str]
        note: str | None = None

    schema = schema_from_model(Nested)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "properties" in schema
    assert schema["properties"]["tags"]["type"] == "array"
    assert "items" in schema["properties"]["tags"]


@pytest.mark.asyncio
async def test_build_agent_rejects_empty_preference() -> None:
    from agentic.binding.pydantic_ai_backend import ModelConfigError, PydanticAIBackend

    with pytest.raises(ModelConfigError, match="empty"):
        PydanticAIBackend._build_agent(
            [],
            output_model=HospitalOut,
            test_output=None,
            system_prompt="x",
        )


@pytest.mark.asyncio
async def test_invoke_rejects_non_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = PydanticAIBackend(
        input_model=HospitalIn,
        output_model=HospitalOut,
        model_config_path=None,
        test_output={"beds_free": 1},
    )

    class _Result:
        output = "not-a-mapping"

    async def _bad_run(_prompt: str) -> _Result:
        return _Result()

    monkeypatch.setattr(backend._agent, "run", _bad_run)
    with pytest.raises(TypeError, match="mapping"):
        await backend.invoke({"patient_id": "p"}, deadline=1.0)


def test_non_test_preference_builds_agent_from_config_string(tmp_path: Path) -> None:
    """Provider id comes from config — construction uses the string as model id."""
    config = tmp_path / "local.toml"
    # Use TestModel via the 'test:' prefix so CI needs no cloud credentials.
    config.write_text('[model]\npreference = ["test:ci"]\n', encoding="utf-8")
    backend = PydanticAIBackend(
        input_model=HospitalIn,
        output_model=HospitalOut,
        model_config_path=config,
        test_output={"beds_free": 3},
    )
    assert backend.declared_schemas()[0]["type"] == "object"


def test_config_provider_string_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-test preference uses the config string as the model (no code pin)."""
    import agentic.binding.pydantic_ai_backend as pai

    captured: dict[str, object] = {}

    class _FakeAgent:
        def __init__(self, model: object, **kwargs: object) -> None:
            captured["model"] = model
            captured["kwargs"] = kwargs

    monkeypatch.setattr(pai, "Agent", _FakeAgent)
    pai.PydanticAIBackend._build_agent(
        ["ollama:llama3.2"],
        output_model=HospitalOut,
        test_output=None,
        system_prompt="structured only",
    )
    assert captured["model"] == "ollama:llama3.2"
