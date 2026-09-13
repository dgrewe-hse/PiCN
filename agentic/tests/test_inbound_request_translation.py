# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""NF-7/NF-4: producer Interest → InboundRequest translation on data_from_lower.

The port-less path dispatches inline via ``_dispatch_event``; the port path
enqueues onto ``_inbound``. Malformed input never raises.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from PiCN.Packets import Content, Interest, Name as PicnName

from agentic.adapters.mock import MockSubstratePort
from agentic.agentic_layer import AgenticLayer
from agentic.port.events import InboundRequest


async def _bare_layer() -> AgenticLayer:
    return AgenticLayer(runtime="async")


def _face_interest(name: str, face_id: int) -> list[object]:
    return [face_id, Interest(PicnName(name))]


@pytest.mark.asyncio
async def test_portless_translates_and_dispatches_with_reply_ref() -> None:
    layer = AgenticLayer(runtime="async")
    seen: list[InboundRequest] = []
    original = layer._on_inbound_request

    async def spy(event: InboundRequest) -> None:
        seen.append(event)
        await original(event)

    layer._on_inbound_request = spy  # type: ignore[method-assign]
    layer.queue_to_lower = asyncio.Queue(maxsize=4)

    face_id = 42
    await layer.data_from_lower(layer.queue_to_lower, None, _face_interest("/cap/x/v=1", face_id))

    assert len(seen) == 1
    event = seen[0]
    expected = hashlib.sha256(b"inbound:" + b"cap/x/v=1").digest()
    assert event.correlation == expected
    assert event.name.components == (b"cap", b"x", b"v=1")
    assert layer._inbound_reply_ref.get(event.correlation) == face_id


@pytest.mark.asyncio
async def test_typed_item_tuple_variant_accepted() -> None:
    layer = AgenticLayer(runtime="async")
    seen: list[InboundRequest] = []
    layer._dispatch_event = _record(seen)  # type: ignore[method-assign]

    await layer.data_from_lower(None, None, (5, Interest(PicnName("/cap/y/v=1"))))
    assert len(seen) == 1
    assert layer._inbound_reply_ref.get(seen[0].correlation) == 5


def _record(sink: list[InboundRequest]):
    async def _dispatch(event):
        assert isinstance(event, InboundRequest)
        sink.append(event)

    return _dispatch


@pytest.mark.asyncio
async def test_port_path_enqueues_onto_inbound() -> None:
    port = MockSubstratePort()
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        await layer.data_from_lower(None, None, _face_interest("/cap/z/v=1", 9))
        # Port-attached: no inline dispatch; the element sits on _inbound.
        event = await asyncio.wait_for(layer._inbound.get(), timeout=1.0)
        assert isinstance(event, InboundRequest)
    finally:
        await layer.stop_port()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        [1],
        [1, 2, 3],
        ["nope", Interest(PicnName("/cap/x/v=1"))],
        [1, Content(PicnName("/cap/x/v=1"), b"c")],
    ],
)
async def test_malformed_returns_without_raising(bad) -> None:
    layer = AgenticLayer(runtime="async")
    await layer.data_from_lower(None, None, bad)
    assert layer._inbound_reply_ref == {}


@pytest.mark.asyncio
async def test_repeated_inbound_same_name_is_deterministic() -> None:
    layer = AgenticLayer(runtime="async")
    seen: list[InboundRequest] = []
    layer._dispatch_event = _record(seen)  # type: ignore[method-assign]
    for _ in range(3):
        await layer.data_from_lower(None, None, _face_interest("/cap/x/v=1", 1))
    assert [e.correlation for e in seen] == [seen[0].correlation] * 3