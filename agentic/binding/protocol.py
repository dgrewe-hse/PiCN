# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""CapabilityBackend Protocol — one shape for every producer backend."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CapabilityBackend(Protocol):
    """Edge producer invoked for a registered capability.

    Deterministic callables and agent-framework adapters both implement this
    Protocol. The invocation path must not branch on backend type.
    """

    def declared_schemas(self) -> tuple[dict[str, Any], dict[str, Any]]:  # pragma: no cover
        """Return ``(input_schema, output_schema)`` for registration conformance.

        Used **only** at registration — never to author a descriptor, and never
        as the runtime validation target.
        """
        ...

    async def invoke(  # pragma: no cover
        self, payload: dict[str, Any], *, deadline: float
    ) -> dict[str, Any]:
        """Execute the capability and return a JSON-object result.

        :param payload: Request body (already validated against the descriptor).
        :param deadline: Absolute deadline in the injected clock domain.
        :return: Result object (validated against the descriptor after return).
        """
        ...


__all__ = ["CapabilityBackend"]
