# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Pydantic AI capability backend (edge producer only).

``pydantic_ai`` imports stay in this module. The forwarding path must never
import them (AC3). Model selection is config-file driven and local-first —
no model id is pinned in code.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import tomllib

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel


class ModelConfigError(ValueError):
    """Raised when model config is missing or unusable."""


def load_model_preference(config_path: Path | str | None) -> list[str]:
    """Load an ordered model preference list from a TOML config file.

    Expected shape::

        [model]
        preference = ["test", "ollama:llama3.2", "openai:gpt-4o-mini"]

    Local / test entries come first by convention; cloud last. When
    ``config_path`` is ``None``, defaults to ``[\"test\"]`` for offline CI.
    """
    if config_path is None:
        return ["test"]
    path = Path(config_path)
    if not path.is_file():
        raise ModelConfigError(f"model config not found: {path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    model = data.get("model")
    if not isinstance(model, dict):
        raise ModelConfigError("config missing [model] table")
    pref = model.get("preference")
    if not isinstance(pref, list) or not pref or not all(isinstance(x, str) for x in pref):
        raise ModelConfigError("model.preference must be a non-empty string list")
    return list(pref)


def _strip_to_profile(schema: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "additionalProperties",
        "title",
        "description",
        "default",
    }
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {
                name: _strip_to_profile(sub) if isinstance(sub, dict) else sub
                for name, sub in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            out[key] = _strip_to_profile(value)
        else:
            out[key] = value
    if "additionalProperties" not in out and out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def schema_from_model(model: type[BaseModel]) -> dict[str, Any]:
    """Derive a restricted-profile schema from a Pydantic model."""
    return _strip_to_profile(model.model_json_schema())


class PydanticAIBackend:
    """Adapt a Pydantic AI agent to :class:`~agentic.binding.protocol.CapabilityBackend`.

    :param input_model: Pydantic model describing accepted inputs.
    :param output_model: Pydantic model describing returned outputs.
    :param model_config_path: Optional TOML path for model preference (local-first).
    :param test_output: Structured output for ``TestModel`` when preference is ``test``.
    :param system_prompt: Optional system prompt for the agent.
    """

    def __init__(
        self,
        *,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        model_config_path: Path | str | None = None,
        test_output: Mapping[str, Any] | None = None,
        system_prompt: str = "Return a valid structured capability result.",
    ) -> None:
        self._input_model = input_model
        self._output_model = output_model
        self._input_schema = schema_from_model(input_model)
        self._output_schema = schema_from_model(output_model)
        preference = load_model_preference(model_config_path)
        self._agent = self._build_agent(
            preference,
            output_model=output_model,
            test_output=test_output,
            system_prompt=system_prompt,
        )

    @staticmethod
    def _build_agent(
        preference: Sequence[str],
        *,
        output_model: type[BaseModel],
        test_output: Mapping[str, Any] | None,
        system_prompt: str,
    ) -> Agent[None, Any]:
        if not preference:
            raise ModelConfigError("empty model preference list")
        first = preference[0]
        if first == "test" or first.startswith("test:"):
            kwargs: dict[str, Any] = {}
            if test_output is not None:
                kwargs["custom_output_args"] = dict(test_output)
            model: Any = TestModel(**kwargs)
        else:
            # Provider string from config — not hard-coded in source.
            model = first
        return Agent(model, output_type=output_model, system_prompt=system_prompt)

    def declared_schemas(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return dict(self._input_schema), dict(self._output_schema)

    async def invoke(self, payload: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        _ = deadline
        validated = self._input_model.model_validate(payload)
        prompt = json.dumps(validated.model_dump(), separators=(",", ":"), sort_keys=True)
        result = await self._agent.run(prompt)
        output = result.output
        if isinstance(output, BaseModel):
            return output.model_dump()
        if isinstance(output, dict):
            return output
        raise TypeError("agent output must be a mapping or BaseModel")


__all__ = [
    "ModelConfigError",
    "PydanticAIBackend",
    "load_model_preference",
    "schema_from_model",
]
