# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""``PicnSubstratePort`` — SubstratePort over the modernized async PiCN stack.

This is the only adapter that may import ``PiCN.*`` broadly (AC2). Events are
always delivered with ``await inbound.put(...)`` — never ``put_nowait``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional

from PiCN.Layers.ChunkLayer import AsyncBasicChunkLayer
from PiCN.Layers.ChunkLayer.Chunkifyer import SimpleContentChunkifyer
from PiCN.Layers.LinkLayer import AsyncBasicLinkLayer
from PiCN.Layers.LinkLayer.FaceIDTable import FaceIDDict
from PiCN.Layers.LinkLayer.Interfaces import AddressInfo, BaseInterface, UDP4Interface
from PiCN.Layers.PacketEncodingLayer import AsyncBasicPacketEncodingLayer
from PiCN.Layers.PacketEncodingLayer.Encoder import BasicEncoder, SimpleStringEncoder
from PiCN.Layers.TimeoutPreventionLayer import (
    AsyncBasicTimeoutPreventionLayer,
    TimeoutPreventionMessageDict,
)
from PiCN.LayerStack.AsyncLayerStack import AsyncLayerStack
from PiCN.Packets import Content, Interest, Nack

from agentic.adapters.picn.names import from_picn_name, to_picn_name
from agentic.port.errors import Unreachable
from agentic.port.events import (
    InboundRequest,
    RequestFailed,
    RequestSent,
    RequestTimedOut,
    ResponseArrived,
    SubstrateEvent,
)
from agentic.port.names import EndpointRef, Match, Name, PrefixTable

Clock = Callable[[], float]
# Forwarder-backed response sink: given (correlation, payload), transmit the
# producer response over the forwarder's own stack. The client ``_lstack`` has
# no ICN layer, so a response cannot simply be pushed onto it (v4 NF-1).
ResponseSink = Callable[[bytes, bytes], Awaitable[None]]


class PicnSubstratePort:
    """UDP / SimulationBus client adapter conforming to ``SubstratePort``.

    Sends Interests and receives Content/Nack via a Fetch-style async PiCN
    stack. Producer-side ``InboundRequest`` events are raised when
    :meth:`inject_interest` is used (AgenticForwarder wiring lands in G.3).

    :param peer_host: Remote ICN forwarder host, or SimulationBus face name.
    :param peer_port: Remote UDP port. Pass ``None`` for SimulationBus peers
        (face address is the string ``peer_host``, matching ``Fetch``).
    :param encoder: Packet encoder (defaults to ``SimpleStringEncoder``).
    :param interfaces: Optional local interfaces (defaults to ``UDP4Interface(0)``).
    :param clock: Injected clock for deadlines / event timestamps.
    :param log_level: PiCN logger level.
    :param response_sink: Optional forwarder-backed response seam; when set,
        :meth:`send_response` delegates to it instead of recording locally.
    """

    def __init__(
        self,
        peer_host: str,
        peer_port: int | None = None,
        *,
        encoder: BasicEncoder | None = None,
        interfaces: list[BaseInterface] | None = None,
        clock: Clock | None = None,
        log_level: int = 255,
        response_sink: ResponseSink | None = None,
    ) -> None:
        self._clock: Clock = clock if clock is not None else time.monotonic
        self._response_sink = response_sink
        self._encoder = encoder if encoder is not None else SimpleStringEncoder(log_level=log_level)
        self._interfaces = interfaces if interfaces is not None else [UDP4Interface(0)]
        faceidtable = FaceIDDict()
        timeoutprevention_dict = TimeoutPreventionMessageDict()
        chunkifyer = SimpleContentChunkifyer()
        self._linklayer = AsyncBasicLinkLayer(
            self._interfaces, faceidtable, log_level=log_level
        )
        self._packetencodinglayer = AsyncBasicPacketEncodingLayer(
            self._encoder, log_level=log_level
        )
        self._chunklayer = AsyncBasicChunkLayer(chunkifyer, log_level=log_level)
        self._timeoutpreventionlayer = AsyncBasicTimeoutPreventionLayer(
            timeoutprevention_dict, None, log_level=log_level
        )
        self._lstack = AsyncLayerStack(
            [
                self._chunklayer,
                self._timeoutpreventionlayer,
                self._packetencodinglayer,
                self._linklayer,
            ]
        )
        # SimulationBus faces use a bare string address (Fetch with port=None).
        if peer_port is None:
            self._fid = self._linklayer.faceidtable.get_or_create_faceid(
                AddressInfo(peer_host, 0)
            )
        else:
            self._fid = self._linklayer.faceidtable.get_or_create_faceid(
                AddressInfo((peer_host, peer_port), 0)
            )
        self._inbound: asyncio.Queue[SubstrateEvent] | None = None
        self._table: dict[Name, list[EndpointRef]] = {}
        self._outstanding: dict[bytes, Name] = {}
        self._deadline_tasks: dict[bytes, asyncio.Task[None]] = {}
        self._response_payloads: dict[bytes, bytes] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._started = False
        self._stack_started = False

    @property
    def interfaces(self) -> list[BaseInterface]:
        return list(self._interfaces)

    async def start(self, inbound: asyncio.Queue[SubstrateEvent]) -> None:
        self._inbound = inbound
        if not self._stack_started:
            self._lstack.start_all()
            self._stack_started = True
        self._started = True
        self._reader_task = asyncio.create_task(
            self._read_uplink(), name="picn-port-uplink"
        )

    async def stop(self) -> None:
        self._started = False
        for correlation in list(self._deadline_tasks):
            self._cancel_deadline(correlation)
        if self._reader_task is not None:
            task = self._reader_task
            self._reader_task = None
            task.cancel()
        if self._stack_started:
            # Fire-and-forget stop: awaiting stop_all can hang when uplink
            # pumps or test monkeypatches never complete.
            try:
                asyncio.create_task(self._lstack.stop_all())
            except Exception:
                pass
            self._stack_started = False
        for iface in self._interfaces:
            close = getattr(iface, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._inbound = None
        self._outstanding.clear()
        await asyncio.sleep(0)

    def parse_name(self, s: str) -> Name:
        parts = [p.encode("utf-8") for p in s.split("/") if p]
        if not parts:
            raise ValueError(f"empty name string: {s!r}")
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
        # Register outstanding BEFORE any await so a fast Content reply cannot
        # race past an empty demux table.
        self._outstanding[correlation] = name
        await self._put(
            RequestSent(correlation=correlation, name=name, at=self._clock())
        )
        interest = Interest(to_picn_name(name))
        try:
            await self._lstack.queue_from_higher.put([self._fid, interest])
        except Exception as exc:  # pragma: no cover - defensive
            self._outstanding.pop(correlation, None)
            await self._put(
                RequestFailed(
                    correlation=correlation,
                    name=name,
                    reason=Unreachable(detail=str(exc)),
                    at=self._clock(),
                )
            )
            return
        timeout = max(0.0, deadline - self._clock())
        self._deadline_tasks[correlation] = asyncio.create_task(
            self._deadline_watch(correlation, name, timeout),
            name=f"picn-deadline-{correlation.hex()[:8]}",
        )
        _ = payload  # reserved for future Interest parameter binding

    async def send_response(self, correlation: bytes, payload: bytes) -> None:
        """Send a producer response for a prior ``InboundRequest``.

        When a forwarder-backed ``response_sink`` is installed, the sink owns
        transmission (Case A: the Content returns through the forwarder's ICN).
        Otherwise the payload is recorded locally for the mock-only path.

        :param correlation: Opaque token from the inbound request.
        :param payload: Response body.
        """
        if self._response_sink is not None:
            await self._response_sink(correlation, payload)
            return
        self._response_payloads[correlation] = payload

    async def inject_interest(
        self, name: Name, payload: bytes, *, correlation: bytes
    ) -> None:
        """Deliver an ``InboundRequest`` (producer path / tests)."""
        await self._put(
            InboundRequest(
                correlation=correlation,
                name=name,
                payload=payload,
                at=self._clock(),
            )
        )

    async def register_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        self._table.setdefault(prefix, []).append(endpoint)

    async def unregister_prefix(self, prefix: Name, endpoint: EndpointRef) -> None:
        endpoints = self._table.get(prefix)
        if endpoints is None:
            return
        self._table[prefix] = [e for e in endpoints if e != endpoint]
        if not self._table[prefix]:
            del self._table[prefix]

    def lookup(self, name: Name) -> Sequence[EndpointRef]:
        match = self.longest_prefix_match(name, self._table)
        return match.endpoints if match else ()

    def _cancel_deadline(self, correlation: bytes) -> None:
        task = self._deadline_tasks.pop(correlation, None)
        if task is not None and not task.done():
            task.cancel()

    async def _deadline_watch(
        self, correlation: bytes, name: Name, timeout: float
    ) -> None:
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        if self._outstanding.pop(correlation, None) is None:
            return
        self._deadline_tasks.pop(correlation, None)
        await self._put(
            RequestTimedOut(correlation=correlation, name=name, at=self._clock())
        )

    def _match_outstanding(self, content_name: Name) -> bytes | None:
        """Return correlation for an outstanding Interest matching ``content_name``.

        Matching is directional longest-prefix (A-012): a Content satisfies an
        Interest iff the Interest name is a component-prefix of the Content
        name. Equality is subsumed by ``is_prefix_of`` (inclusive). The old
        reverse clause is deleted: it is not valid ICN semantics and, for
        prefix-overlapping Interests such as ``.../h1`` and ``.../h10``,
        returned the wrong correlation in dict order and corrupted the Merkle
        trace root.
        """
        for correlation, interest_name in self._outstanding.items():
            if interest_name.is_prefix_of(content_name):
                return correlation
        return None

    async def _deliver_uplink_packet(self, packet: Any) -> None:
        body = packet[1] if isinstance(packet, (list, tuple)) else packet
        if isinstance(body, Content):
            content_name = from_picn_name(body.name)
            # Match on the wire name directly: the port's Name is components of
            # the PiCN name, so no re-encoding is needed.
            correlation = self._match_outstanding(content_name)
            if correlation is None:
                return
            self._outstanding.pop(correlation, None)
            self._cancel_deadline(correlation)
            content = body.content
            if isinstance(content, str):
                raw = content.encode("utf-8")
            elif isinstance(content, bytes):
                raw = content
            else:
                raw = str(content).encode("utf-8")
            await self._put(
                ResponseArrived(
                    correlation=correlation,
                    name=content_name,
                    payload=raw,
                    at=self._clock(),
                )
            )
            return
        if isinstance(body, Nack):
            # Prefer name-based correlation (directional LPM) over dict order;
            # drop when nothing matches rather than mis-attributing. The wire
            # carries no token (A-012), so an unmatched Nack is not deliverable.
            nack_name = from_picn_name(body.name)
            correlation = self._match_outstanding(nack_name)
            if correlation is None:
                return
            name = self._outstanding.pop(correlation, nack_name)
            self._cancel_deadline(correlation)
            await self._put(
                RequestFailed(
                    correlation=correlation,
                    name=name,
                    reason=Unreachable(detail=f"nack:{body.reason}"),
                    at=self._clock(),
                )
            )
            return

    async def _put(self, event: SubstrateEvent) -> None:
        if self._inbound is None:
            # Soft-drop during shutdown; hard-fail if never started.
            if not self._started:
                return
            raise RuntimeError("PicnSubstratePort.start() must be called first")
        await self._inbound.put(event)

    async def _read_uplink(self) -> None:
        """Demux uplink Content/Nack onto outstanding Interests by name."""
        try:
            while True:
                packet = await self._lstack.queue_to_higher.get()
                if not self._started:
                    return
                await self._deliver_uplink_packet(packet)
        except asyncio.CancelledError:
            raise


__all__ = ["PicnSubstratePort"]
