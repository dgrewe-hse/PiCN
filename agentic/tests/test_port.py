# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Unit tests for the substrate port types and Protocol conformance."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from typing import Optional, assert_type, get_args

import pytest

from agentic.port import (
    AgenticError,
    AggregationFailed,
    BoundExceeded,
    CapacityExhausted,
    DescriptorInvalid,
    EndpointRef,
    InboundRequest,
    Malformed,
    Match,
    Name,
    NoTemplateMatched,
    PrefixTable,
    RequestFailed,
    RequestSent,
    RequestTimedOut,
    ResponseArrived,
    SubstrateError,
    SubstrateEvent,
    SubstratePort,
    TransportClosed,
    Unreachable,
)


def _name(*parts: str) -> Name:
    return Name(tuple(p.encode("utf-8") for p in parts))


# ---------------------------------------------------------------------------
# Name / Match / EndpointRef
# ---------------------------------------------------------------------------


def test_name_is_frozen() -> None:
    name = _name("cap", "issuer", "svc")
    with pytest.raises(FrozenInstanceError):
        name.components = ()  # type: ignore[misc]


def test_name_is_prefix_of() -> None:
    prefix = _name("cap", "issuer")
    longer = _name("cap", "issuer", "svc", "v=1")
    other = _name("cap", "other")
    assert prefix.is_prefix_of(longer)
    assert prefix.is_prefix_of(prefix)
    assert not longer.is_prefix_of(prefix)
    assert not prefix.is_prefix_of(other)


def test_endpoint_ref_and_match_fields() -> None:
    endpoint = EndpointRef(value=b"face-1")
    match = Match(prefix=_name("cap"), endpoints=(endpoint,))
    assert match.prefix.components == (b"cap",)
    assert match.endpoints == (endpoint,)
    with pytest.raises(FrozenInstanceError):
        match.prefix = _name("x")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Events — frozen, correct fields, closed union
# ---------------------------------------------------------------------------


def test_event_union_is_exactly_five_types() -> None:
    assert set(get_args(SubstrateEvent)) == {
        RequestSent,
        ResponseArrived,
        RequestTimedOut,
        RequestFailed,
        InboundRequest,
    }


def test_request_sent_fields_and_frozen() -> None:
    event = RequestSent(correlation=b"\x01", name=_name("a"), at=1.5)
    assert event.correlation == b"\x01"
    assert event.name == _name("a")
    assert event.at == 1.5
    with pytest.raises(FrozenInstanceError):
        event.at = 2.0  # type: ignore[misc]


def test_response_arrived_fields() -> None:
    event = ResponseArrived(
        correlation=b"c",
        name=_name("n"),
        payload=b"body",
        at=0.0,
    )
    assert event.payload == b"body"


def test_request_timed_out_fields() -> None:
    event = RequestTimedOut(correlation=b"c", name=_name("n"), at=9.0)
    assert event.correlation == b"c"


def test_request_failed_carries_substrate_error_only() -> None:
    reason = Unreachable(detail="no route")
    event = RequestFailed(
        correlation=b"c",
        name=_name("n"),
        reason=reason,
        at=3.0,
    )
    assert isinstance(event.reason, SubstrateError)
    assert not isinstance(event.reason, AgenticError)
    assert event.reason.detail == "no route"


def test_inbound_request_fields() -> None:
    event = InboundRequest(
        correlation=b"c",
        name=_name("n"),
        payload=b"req",
        at=4.0,
    )
    assert event.payload == b"req"


# ---------------------------------------------------------------------------
# Error families are disjoint
# ---------------------------------------------------------------------------


def test_substrate_error_family() -> None:
    for cls in (Unreachable, Malformed, TransportClosed):
        err = cls(detail="x")
        assert isinstance(err, SubstrateError)
        assert not isinstance(err, AgenticError)


def test_agentic_error_family() -> None:
    for cls in (
        NoTemplateMatched,
        BoundExceeded,
        DescriptorInvalid,
        AggregationFailed,
        CapacityExhausted,
    ):
        err = cls(detail="x")
        assert isinstance(err, AgenticError)
        assert not isinstance(err, SubstrateError)


# ---------------------------------------------------------------------------
# Protocol conformance — structural stub checked by mypy via assert_type
# ---------------------------------------------------------------------------


class _ConformingStub:
    """Minimal structural implementation used only for type/conformance tests."""

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[SubstrateEvent] | None = None
        self._table: dict[Name, list[EndpointRef]] = {}

    async def start(self, inbound: asyncio.Queue[SubstrateEvent]) -> None:
        self._inbound = inbound

    async def stop(self) -> None:
        self._inbound = None

    def parse_name(self, s: str) -> Name:
        parts = [p.encode("utf-8") for p in s.split("/") if p]
        return Name(tuple(parts))

    def name_from_components(self, parts: Sequence[bytes]) -> Name:
        return Name(tuple(parts))

    def longest_prefix_match(
        self, name: Name, table: PrefixTable
    ) -> Optional[Match]:
        best: Match | None = None
        for prefix, endpoints in table.items():
            if prefix.is_prefix_of(name):
                if best is None or len(prefix) > len(best.prefix):
                    best = Match(prefix=prefix, endpoints=tuple(endpoints))
        return best

    async def send_request(
        self,
        name: Name,
        payload: bytes,
        *,
        correlation: bytes,
        deadline: float,
    ) -> None:
        assert self._inbound is not None
        await self._inbound.put(
            RequestSent(correlation=correlation, name=name, at=deadline)
        )

    async def send_response(self, correlation: bytes, payload: bytes) -> None:
        return None

    async def register_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        self._table.setdefault(prefix, []).append(endpoint)

    async def unregister_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        endpoints = self._table.get(prefix, [])
        self._table[prefix] = [e for e in endpoints if e != endpoint]

    def lookup(self, name: Name) -> Sequence[EndpointRef]:
        match = self.longest_prefix_match(name, self._table)
        return match.endpoints if match else ()


def test_stub_is_runtime_checkable_substrate_port() -> None:
    stub: SubstratePort = _ConformingStub()
    assert isinstance(stub, SubstratePort)


def test_assert_type_conformance() -> None:
    """Compile-time check: mypy verifies the stub satisfies SubstratePort."""
    stub: SubstratePort = _ConformingStub()
    assert_type(stub, SubstratePort)


@pytest.mark.asyncio
async def test_stub_start_and_send_request_enqueue_event() -> None:
    stub = _ConformingStub()
    inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=8)
    await stub.start(inbound)
    await stub.send_request(
        _name("cap", "x"),
        b"",
        correlation=b"\xab",
        deadline=1.0,
    )
    event = await asyncio.wait_for(inbound.get(), timeout=1.0)
    assert isinstance(event, RequestSent)
    assert event.correlation == b"\xab"
    await stub.stop()
