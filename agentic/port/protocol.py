# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""``SubstratePort`` — structural Protocol for forwarder adapters.

Adapters push typed events into a bounded ``asyncio.Queue`` owned by the
agentic layer (passed to :meth:`SubstratePort.start`). Failures become
``RequestFailed`` events; adapters must not raise across this boundary.
Always ``await queue.put(event)`` — never ``put_nowait``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Optional, Protocol, runtime_checkable

from agentic.port.events import SubstrateEvent
from agentic.port.names import EndpointRef, Match, Name, PrefixTable


@runtime_checkable
class SubstratePort(Protocol):
    """Minimal contract a network substrate must satisfy for the agentic layer.

    Structural Protocol (not an ABC): adapters conform by shape; ``mypy``
    proves conformance. Method groups: lifecycle, naming, request/response,
    forwarding table.
    """

    # --- lifecycle ---------------------------------------------------------

    async def start(self, inbound: asyncio.Queue[SubstrateEvent]) -> None:
        """Begin delivering events into ``inbound``.

        :param inbound: Bounded queue owned by the agentic layer. The adapter
            is the sole producer; the layer is the sole consumer.
        """
        ...

    async def stop(self) -> None:
        """Stop delivering events and release substrate resources."""
        ...

    # --- naming ------------------------------------------------------------

    def parse_name(self, s: str) -> Name:
        """Parse a human-readable name string into a hierarchical ``Name``.

        :param s: Substrate-conventional string form (adapter-defined).
        :return: Parsed hierarchical name.
        :raises ValueError: If ``s`` cannot be parsed by this adapter.
        """
        ...

    def name_from_components(self, parts: Sequence[bytes]) -> Name:
        """Build a ``Name`` from raw components.

        :param parts: Ordered components.
        :return: Hierarchical name.
        """
        ...

    def longest_prefix_match(
        self, name: Name, table: PrefixTable
    ) -> Optional[Match]:
        """Longest-prefix match of ``name`` against ``table``.

        :param name: Query name.
        :param table: Prefix → endpoints mapping.
        :return: Longest matching prefix and its endpoints, or ``None``.
        """
        ...

    # --- request / response ------------------------------------------------

    async def send_request(
        self,
        name: Name,
        payload: bytes,
        *,
        correlation: bytes,
        deadline: float,
    ) -> None:
        """Send a request under ``name``.

        On acceptance the adapter pushes ``RequestSent``. Outcomes arrive as
        ``ResponseArrived``, ``RequestTimedOut``, or ``RequestFailed``.
        ``correlation`` is opaque — echo it unchanged on every related event.

        :param name: Request name.
        :param payload: Request body.
        :param correlation: Opaque token owned by the agentic layer.
        :param deadline: Absolute deadline in the same clock domain as event
            ``at`` timestamps.
        """
        ...

    async def send_response(self, correlation: bytes, payload: bytes) -> None:
        """Send a response for a previously delivered ``InboundRequest``.

        :param correlation: Opaque token from the inbound request event.
        :param payload: Response body.
        """
        ...

    # --- forwarding table --------------------------------------------------

    async def register_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        """Advertise ``prefix`` as reachable via ``endpoint``.

        :param prefix: Name prefix to register.
        :param endpoint: Opaque endpoint handle.
        """
        ...

    async def unregister_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        """Withdraw a previously registered prefix/endpoint pair.

        Mid-flight revocation of in-progress intents is out of scope for v0.1;
        this only updates the forwarding table.

        :param prefix: Name prefix to withdraw.
        :param endpoint: Opaque endpoint handle previously registered.
        """
        ...

    def lookup(self, name: Name) -> Sequence[EndpointRef]:
        """Return currently registered endpoints that could serve ``name``.

        :param name: Query name.
        :return: Matching endpoints (possibly empty); order is adapter-defined.
        """
        ...
