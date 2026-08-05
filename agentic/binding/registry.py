# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Capability registration and descriptor-authoritative invocation.

Backends are interchangeable behind :class:`~agentic.binding.protocol.CapabilityBackend`.
The invocation path never branches on backend implementation type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic.agentic_layer.descriptor import CapabilityDescriptor
from agentic.binding.protocol import CapabilityBackend
from agentic.binding.schema import (
    SchemaCompatibilityError,
    SchemaProfileError,
    schemas_compatible,
    validate_against_schema,
)
from agentic.port.errors import DescriptorInvalid
from agentic.port.names import Name


@dataclass(frozen=True, slots=True)
class BindingResponse:
    """Result of a capability invocation through the binding layer.

    :param payload: Result object when invocation returned; ``None`` on input reject.
    :param quote_id: Attestation quote reference only (AC6 — never an embedded quote).
    :param output_valid: False when the backend returned a descriptor-invalid body
        (recorded as a reputation observation by the caller; not a crash).
    :param input_rejected: True when the request failed descriptor input validation.
    """

    payload: dict[str, Any] | None
    quote_id: str | None
    output_valid: bool
    input_rejected: bool = False


@dataclass
class CapabilityRegistration:
    """One registered producer for a capability name."""

    descriptor: CapabilityDescriptor
    backend: CapabilityBackend
    backend_label: str = "unspecified"


@dataclass
class CapabilityRegistry:
    """Register backends against authoritative descriptors; invoke uniformly."""

    _by_name: dict[Name, CapabilityRegistration] = field(default_factory=dict)

    def register(
        self,
        descriptor: CapabilityDescriptor,
        backend: CapabilityBackend,
        *,
        backend_label: str = "unspecified",
    ) -> None:
        """Register ``backend`` if it conforms to ``descriptor`` schemas.

        :raises DescriptorInvalid: On profile or variance failure.
        """
        try:
            in_s, out_s = backend.declared_schemas()
            schemas_compatible(
                descriptor_input=descriptor.input_schema,
                descriptor_output=descriptor.output_schema,
                backend_input=in_s,
                backend_output=out_s,
            )
        except (SchemaProfileError, SchemaCompatibilityError) as exc:
            raise DescriptorInvalid(detail=str(exc)) from exc
        self._by_name[descriptor.name] = CapabilityRegistration(
            descriptor=descriptor,
            backend=backend,
            backend_label=backend_label,
        )

    def get(self, name: Name) -> CapabilityRegistration | None:
        return self._by_name.get(name)

    async def invoke(
        self,
        name: Name,
        payload: dict[str, Any],
        *,
        deadline: float,
        quote_id: str | None = None,
    ) -> BindingResponse:
        """Validate against the **descriptor**, invoke, validate output.

        Quote is attached by reference only. Output failures set
        ``output_valid=False`` instead of raising past this layer.
        """
        reg = self._by_name.get(name)
        if reg is None:
            raise DescriptorInvalid(detail=f"no backend registered for {name!r}")
        try:
            validate_against_schema(payload, reg.descriptor.input_schema)
        except DescriptorInvalid:
            return BindingResponse(
                payload=None,
                quote_id=quote_id,
                output_valid=False,
                input_rejected=True,
            )
        result = await reg.backend.invoke(payload, deadline=deadline)
        try:
            validate_against_schema(result, reg.descriptor.output_schema)
            output_valid = True
        except DescriptorInvalid:
            output_valid = False
        return BindingResponse(
            payload=result,
            quote_id=quote_id,
            output_valid=output_valid,
            input_rejected=False,
        )


__all__ = [
    "BindingResponse",
    "CapabilityRegistration",
    "CapabilityRegistry",
]
