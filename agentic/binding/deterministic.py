# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Deterministic capability backend (plain async/sync callable)."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

Handler = Callable[..., dict[str, Any] | Awaitable[dict[str, Any]]]


class DeterministicBackend:
    """Wrap a typed callable as a :class:`CapabilityBackend`.

    :param handler: ``(payload) -> dict`` or async equivalent.
    :param input_schema: Declared input schema (registration check only).
    :param output_schema: Declared output schema (registration check only).
    """

    def __init__(
        self,
        handler: Handler,
        *,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> None:
        self._handler = handler
        self._input_schema = dict(input_schema)
        self._output_schema = dict(output_schema)

    def declared_schemas(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return dict(self._input_schema), dict(self._output_schema)

    async def invoke(self, payload: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        result = self._handler(payload)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise TypeError("deterministic handler must return a dict")
        return result


__all__ = ["DeterministicBackend", "Handler"]
