# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Capability producer registration — the agent plug-in surface.

A producer is a signed capability descriptor plus a
:class:`~agentic.binding.protocol.CapabilityBackend`. The fabric cannot tell
deterministic handlers from agent-framework backends (AC3: LLM imports stay
in ``binding/`` only).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agentic.agentic_layer.cfib import CapabilityFIB
from agentic.agentic_layer.descriptor import CapabilityDescriptor
from agentic.binding.protocol import CapabilityBackend
from agentic.binding.registry import BindingResponse, CapabilityRegistry
from agentic.port.names import Name

QuoteIdProvider = Callable[[], str | None | Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class RegisteredProducer:
    """One locally served capability.

    :param descriptor: Authoritative descriptor (registration-cached).
    :param backend_label: Opaque label for run metadata (never used for branching).
    """

    descriptor: CapabilityDescriptor
    backend_label: str


class CapabilityProducerHub:
    """Register backends against descriptors and expose invoke helpers.

    :param registry: Shared binding registry (descriptor-authoritative invoke).
    :param cfib: Capability FIB updated on each successful registration.
    """

    def __init__(
        self,
        *,
        registry: CapabilityRegistry | None = None,
        cfib: CapabilityFIB | None = None,
    ) -> None:
        self.registry = registry if registry is not None else CapabilityRegistry()
        self.cfib = cfib if cfib is not None else CapabilityFIB()
        self._labels: dict[Name, str] = {}

    def register(
        self,
        descriptor: CapabilityDescriptor,
        backend: CapabilityBackend,
        *,
        backend_label: str = "unspecified",
    ) -> RegisteredProducer:
        """Register ``backend`` if it conforms to ``descriptor``.

        :param descriptor: Authoritative capability descriptor.
        :param backend: Deterministic or agent-framework backend.
        :param backend_label: Metadata label (e.g. ``deterministic``, ``pydantic-ai``).
        :return: Registration receipt.
        :raises DescriptorInvalid: On schema / variance failure.
        """
        self.registry.register(descriptor, backend, backend_label=backend_label)
        self.cfib.register(descriptor)
        self._labels[descriptor.name] = backend_label
        return RegisteredProducer(descriptor=descriptor, backend_label=backend_label)

    def backend_label(self, name: Name) -> str | None:
        return self._labels.get(name)

    async def invoke(
        self,
        name: Name,
        payload: dict[str, Any],
        *,
        deadline: float,
        quote_id: str | None = None,
    ) -> BindingResponse:
        """Invoke through the shared registry (identical path for every backend)."""
        return await self.registry.invoke(
            name, payload, deadline=deadline, quote_id=quote_id
        )


__all__ = [
    "CapabilityProducerHub",
    "QuoteIdProvider",
    "RegisteredProducer",
]
