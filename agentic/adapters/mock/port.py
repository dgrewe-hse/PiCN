# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""In-memory ``SubstratePort`` for unit and integration tests.

Deterministic delivery, injectable failures, injectable clock, and a hold /
ordered-release mode so out-of-order response arrival can be exercised without
a network. Substrate failures become ``RequestFailed`` events — nothing is
raised across the port boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from agentic.adapters.mock.clock import Clock, ManualClock
from agentic.port.errors import Malformed, SubstrateError, TransportClosed, Unreachable
from agentic.port.events import (
    InboundRequest,
    RequestFailed,
    RequestSent,
    RequestTimedOut,
    ResponseArrived,
    SubstrateEvent,
)
from agentic.port.names import EndpointRef, Match, Name, PrefixTable


class _OutcomeKind(Enum):
    RESPONSE = auto()
    TIMEOUT = auto()
    FAIL = auto()


@dataclass
class _PendingTerminal:
    """Terminal outcome waiting to be pushed (possibly under hold)."""

    correlation: bytes
    name: Name
    kind: _OutcomeKind
    payload: bytes = b""
    reason: SubstrateError | None = None


@dataclass
class _OutstandingRequest:
    name: Name
    payload: bytes
    deadline: float


class MockSubstratePort:
    """In-memory adapter conforming to ``SubstratePort``.

    :param clock: Callable returning the current time. Defaults to a
        :class:`ManualClock` frozen at ``0.0``.
    :param hold_terminals: When True, ``RequestSent`` is delivered immediately
        but terminal events (response / timeout / failure) are buffered until
        :meth:`release_terminals` is called with an explicit order.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        hold_terminals: bool = False,
    ) -> None:
        self._clock: Clock = clock if clock is not None else ManualClock(0.0)
        self._hold_terminals = hold_terminals
        self._inbound: asyncio.Queue[SubstrateEvent] | None = None
        self._table: dict[Name, list[EndpointRef]] = {}
        self._outstanding: dict[bytes, _OutstandingRequest] = {}
        self._buffered: dict[bytes, _PendingTerminal] = {}
        self._scripted_payloads: dict[bytes, bytes] = {}
        self._next_failures: list[SubstrateError] = []
        self._correlation_failures: dict[bytes, SubstrateError] = {}
        self._next_timeouts: int = 0
        self._correlation_timeouts: set[bytes] = set()
        self._inbound_payloads_seen: dict[bytes, bytes] = {}
        self._responses_sent: dict[bytes, bytes] = {}
        self._started = False

    # --- test controls -----------------------------------------------------

    @property
    def hold_terminals(self) -> bool:
        """Whether terminal events are buffered for ordered release."""
        return self._hold_terminals

    @hold_terminals.setter
    def hold_terminals(self, value: bool) -> None:
        self._hold_terminals = value

    def script_response(self, correlation: bytes, payload: bytes) -> None:
        """Set the payload returned for ``correlation`` when auto-completing.

        :param correlation: Opaque correlation token.
        :param payload: Response body to deliver as ``ResponseArrived``.
        """
        self._scripted_payloads[correlation] = payload

    def fail_next(self, reason: SubstrateError | None = None) -> None:
        """Fail the next ``send_request`` with ``reason`` (default Unreachable).

        :param reason: Substrate error to place in ``RequestFailed``.
        """
        self._next_failures.append(reason if reason is not None else Unreachable())

    def fail_on(self, correlation: bytes, reason: SubstrateError | None = None) -> None:
        """Fail the request with the given correlation when it is sent.

        :param correlation: Opaque correlation token.
        :param reason: Substrate error to place in ``RequestFailed``.
        """
        self._correlation_failures[correlation] = (
            reason if reason is not None else Unreachable()
        )

    def timeout_next(self) -> None:
        """Make the next ``send_request`` complete as ``RequestTimedOut``."""
        self._next_timeouts += 1

    def timeout_on(self, correlation: bytes) -> None:
        """Make the request with ``correlation`` complete as ``RequestTimedOut``.

        :param correlation: Opaque correlation token.
        """
        self._correlation_timeouts.add(correlation)

    async def inject_inbound_request(
        self,
        name: Name,
        payload: bytes,
        *,
        correlation: bytes,
    ) -> None:
        """Push an ``InboundRequest`` as if a remote peer asked this node.

        :param name: Requested name.
        :param payload: Request body.
        :param correlation: Opaque token for the subsequent ``send_response``.
        """
        self._inbound_payloads_seen[correlation] = payload
        await self._put(
            InboundRequest(
                correlation=correlation,
                name=name,
                payload=payload,
                at=self._clock(),
            )
        )

    async def release_terminals(self, order: Sequence[bytes]) -> None:
        """Deliver buffered terminal events in ``order``.

        Correlations not listed are left buffered. Unknown correlations are
        ignored. Used to exercise out-of-order arrival deterministically.

        :param order: Correlation tokens in the desired delivery order.
        :raises RuntimeError: If the adapter has not been started.
        """
        for correlation in order:
            pending = self._buffered.pop(correlation, None)
            if pending is None:
                continue
            await self._emit_terminal(pending)

    def buffered_correlations(self) -> tuple[bytes, ...]:
        """Return correlations with buffered terminal events (test helper)."""
        return tuple(self._buffered.keys())

    @property
    def responses_sent(self) -> dict[bytes, bytes]:
        """Payloads passed to :meth:`send_response`, keyed by correlation."""
        return dict(self._responses_sent)

    # --- lifecycle ---------------------------------------------------------

    async def start(self, inbound: asyncio.Queue[SubstrateEvent]) -> None:
        """Begin delivering events into ``inbound``.

        :param inbound: Bounded queue owned by the agentic layer.
        """
        self._inbound = inbound
        self._started = True

    async def stop(self) -> None:
        """Stop delivering events and drop outstanding state."""
        self._started = False
        self._inbound = None
        self._outstanding.clear()
        self._buffered.clear()

    # --- naming ------------------------------------------------------------

    def parse_name(self, s: str) -> Name:
        """Parse a slash-separated name string into a ``Name``.

        Empty segments (leading/trailing/duplicate slashes) are skipped.
        Non-UTF-8 input is not expected; use :meth:`name_from_components`
        for raw bytes.

        :param s: Slash-separated hierarchical name.
        :return: Parsed name.
        :raises ValueError: If ``s`` yields no components.
        """
        parts = [p.encode("utf-8") for p in s.split("/") if p]
        if not parts:
            raise ValueError(f"empty name string: {s!r}")
        return Name(tuple(parts))

    def name_from_components(self, parts: Sequence[bytes]) -> Name:
        """Build a ``Name`` from raw components.

        :param parts: Ordered components.
        :return: Hierarchical name.
        """
        return Name(tuple(parts))

    def longest_prefix_match(
        self, name: Name, table: PrefixTable
    ) -> Optional[Match]:
        """Longest-prefix match of ``name`` against ``table``.

        :param name: Query name.
        :param table: Prefix → endpoints mapping.
        :return: Longest matching prefix and its endpoints, or ``None``.
        """
        best: Match | None = None
        for prefix, endpoints in table.items():
            if prefix.is_prefix_of(name):
                if best is None or len(prefix) > len(best.prefix):
                    best = Match(prefix=prefix, endpoints=tuple(endpoints))
        return best

    # --- request / response ------------------------------------------------

    async def send_request(
        self,
        name: Name,
        payload: bytes,
        *,
        correlation: bytes,
        deadline: float,
    ) -> None:
        """Send a request; emit ``RequestSent`` then a terminal outcome.

        Failures and timeouts are events, never exceptions to the caller.

        :param name: Request name.
        :param payload: Request body.
        :param correlation: Opaque token owned by the agentic layer.
        :param deadline: Absolute deadline in the injected clock domain.
        """
        await self._put(
            RequestSent(correlation=correlation, name=name, at=self._clock())
        )
        self._outstanding[correlation] = _OutstandingRequest(
            name=name, payload=payload, deadline=deadline
        )
        terminal = self._plan_terminal(correlation, name, deadline)
        if self._hold_terminals:
            self._buffered[correlation] = terminal
            return
        await self._emit_terminal(terminal)

    async def send_response(self, correlation: bytes, payload: bytes) -> None:
        """Record a producer response for a prior ``InboundRequest``.

        :param correlation: Opaque token from the inbound request.
        :param payload: Response body.
        """
        # No raise if unknown — substrate would typically drop; tests inspect
        # responses_sent. Recording is enough for the mock.
        self._responses_sent[correlation] = payload

    # --- forwarding table --------------------------------------------------

    async def register_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        """Advertise ``prefix`` as reachable via ``endpoint``.

        :param prefix: Name prefix to register.
        :param endpoint: Opaque endpoint handle.
        """
        self._table.setdefault(prefix, []).append(endpoint)

    async def unregister_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        """Withdraw a previously registered prefix/endpoint pair.

        :param prefix: Name prefix to withdraw.
        :param endpoint: Opaque endpoint handle previously registered.
        """
        endpoints = self._table.get(prefix)
        if endpoints is None:
            return
        self._table[prefix] = [e for e in endpoints if e != endpoint]
        if not self._table[prefix]:
            del self._table[prefix]

    def lookup(self, name: Name) -> Sequence[EndpointRef]:
        """Return endpoints registered under the longest matching prefix.

        :param name: Query name.
        :return: Matching endpoints, or an empty sequence.
        """
        match = self.longest_prefix_match(name, self._table)
        return match.endpoints if match else ()

    # --- internals ---------------------------------------------------------

    def _plan_terminal(
        self, correlation: bytes, name: Name, deadline: float
    ) -> _PendingTerminal:
        if correlation in self._correlation_failures:
            reason = self._correlation_failures.pop(correlation)
            return _PendingTerminal(
                correlation=correlation,
                name=name,
                kind=_OutcomeKind.FAIL,
                reason=reason,
            )
        if self._next_failures:
            reason = self._next_failures.pop(0)
            return _PendingTerminal(
                correlation=correlation,
                name=name,
                kind=_OutcomeKind.FAIL,
                reason=reason,
            )
        if correlation in self._correlation_timeouts:
            self._correlation_timeouts.discard(correlation)
            return _PendingTerminal(
                correlation=correlation,
                name=name,
                kind=_OutcomeKind.TIMEOUT,
            )
        if self._next_timeouts > 0:
            self._next_timeouts -= 1
            return _PendingTerminal(
                correlation=correlation,
                name=name,
                kind=_OutcomeKind.TIMEOUT,
            )
        if self._clock() > deadline:
            return _PendingTerminal(
                correlation=correlation,
                name=name,
                kind=_OutcomeKind.TIMEOUT,
            )
        payload = self._scripted_payloads.pop(correlation, b"")
        return _PendingTerminal(
            correlation=correlation,
            name=name,
            kind=_OutcomeKind.RESPONSE,
            payload=payload,
        )

    async def _emit_terminal(self, pending: _PendingTerminal) -> None:
        self._outstanding.pop(pending.correlation, None)
        at = self._clock()
        if pending.kind is _OutcomeKind.RESPONSE:
            await self._put(
                ResponseArrived(
                    correlation=pending.correlation,
                    name=pending.name,
                    payload=pending.payload,
                    at=at,
                )
            )
        elif pending.kind is _OutcomeKind.TIMEOUT:
            await self._put(
                RequestTimedOut(
                    correlation=pending.correlation,
                    name=pending.name,
                    at=at,
                )
            )
        else:
            reason = pending.reason if pending.reason is not None else Unreachable()
            await self._put(
                RequestFailed(
                    correlation=pending.correlation,
                    name=pending.name,
                    reason=reason,
                    at=at,
                )
            )

    async def _put(self, event: SubstrateEvent) -> None:
        if self._inbound is None or not self._started:
            raise RuntimeError("MockSubstratePort.start() must be called first")
        await self._inbound.put(event)


# Re-export failure types tests commonly inject, without encouraging adapters
# to import agentic error families.
__all__ = [
    "MockSubstratePort",
    "Malformed",
    "TransportClosed",
    "Unreachable",
]
