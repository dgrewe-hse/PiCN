# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Async-only agentic layer sitting above NFN in the stack.

Owns the inbound substrate event queue, capability producer hub, Context PIT,
and (optional) decomposer. Communicates with the network only through a
:class:`~agentic.port.protocol.SubstratePort` — never through adapter internals.

An agentic node must use ``runtime=\"async\"`` — see :func:`require_async_runtime`.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from typing import Any

from PiCN.Packets import Content, Name as PicnName, Interest
from PiCN.Processes.AsyncLayerProcess import AsyncLayerProcess
from PiCN.Processes.Outbound import Outbound

from agentic.agentic_layer.aggregation import maybe_complete
from agentic.agentic_layer.cfib import CapabilityFIB
from agentic.agentic_layer.context_pit import ContextPIT
from agentic.agentic_layer.decomposer import BoundedDecomposer, SubIntent
from agentic.agentic_layer.descriptor import CapabilityDescriptor
from agentic.agentic_layer.naming import CapabilityNamingError, parse_capability_name
from agentic.agentic_layer.producer import CapabilityProducerHub, QuoteIdProvider
from agentic.agentic_layer.runtime import require_async_runtime
from agentic.binding.protocol import CapabilityBackend
from agentic.port.events import (
    InboundRequest,
    RequestFailed,
    RequestSent,
    RequestTimedOut,
    ResponseArrived,
    SubstrateEvent,
)
from agentic.port.names import Name
from agentic.port.protocol import SubstratePort
from agentic.trust.merkle import NULL_RESPONSE

# Default bound for the layer-owned inbound event queue (ADR-005 style).
DEFAULT_INBOUND_SIZE: int = 64


class AgenticLayer(AsyncLayerProcess):
    """Topmost async layer for capability routing and bounded decomposition.

    :param log_level: PiCN logger level (255 = quiet).
    :param runtime: Must be ``\"async\"``; sync is refused explicitly.
    :param port: Optional substrate port (mock or PiCN adapter).
    :param inbound_size: Bound for the port event queue.
    :param quote_id_provider: Optional callback supplying attestation quote ids.
    """

    def __init__(
        self,
        log_level: int = 255,
        *,
        runtime: str = "async",
        port: SubstratePort | None = None,
        inbound_size: int = DEFAULT_INBOUND_SIZE,
        quote_id_provider: QuoteIdProvider | None = None,
    ) -> None:
        require_async_runtime(runtime)
        super().__init__(logger_name="AgenticLayer", log_level=log_level)
        self._port = port
        self._inbound: asyncio.Queue[SubstrateEvent] = asyncio.Queue(maxsize=inbound_size)
        self._hub = CapabilityProducerHub()
        self._pit = ContextPIT()
        self._decomposer: BoundedDecomposer | None = None
        self._quote_id_provider = quote_id_provider
        self._event_task: asyncio.Task[None] | None = None
        self._leaf_index: dict[bytes, tuple[bytes, int]] = {}
        self._parent_done: dict[bytes, asyncio.Future[bytes]] = {}
        self._started_port = False
        # Inbound reply-face side table (NF-4 Option 2): keyed by the inbound
        # correlation, holds the face id the response must be returned to.
        # Kept off the frozen InboundRequest union so both adapters stay
        # untouched. Emptied on stop_port.
        self._inbound_reply_ref: dict[bytes, int] = {}

    @property
    def hub(self) -> CapabilityProducerHub:
        return self._hub

    @property
    def cfib(self) -> CapabilityFIB:
        return self._hub.cfib

    @property
    def pit(self) -> ContextPIT:
        return self._pit

    @property
    def port(self) -> SubstratePort | None:
        return self._port

    def attach_port(self, port: SubstratePort) -> None:
        """Attach a substrate port before :meth:`start_port`.

        :param port: Mock or PiCN adapter conforming to ``SubstratePort``.
        """
        if self._started_port:
            raise RuntimeError("cannot attach port after start_port")
        self._port = port

    def set_decomposer(self, decomposer: BoundedDecomposer) -> None:
        """Install a bounded decomposer for multi-leaf intents."""
        self._decomposer = decomposer

    def register_capability(
        self,
        descriptor: CapabilityDescriptor,
        backend: CapabilityBackend,
        *,
        backend_label: str = "unspecified",
    ) -> None:
        """Plug an agent (or deterministic handler) into this node.

        :param descriptor: Authoritative signed capability descriptor.
        :param backend: Backend implementing ``CapabilityBackend``.
        :param backend_label: Run-metadata label only (no fabric branching).
        """
        self._hub.register(descriptor, backend, backend_label=backend_label)

    async def start_port(self) -> None:
        """Start the port and the inbound event pump."""
        if self._port is None:
            raise RuntimeError("no SubstratePort attached")
        if self._started_port:
            return
        await self._port.start(self._inbound)
        self._started_port = True
        self._event_task = asyncio.create_task(
            self._event_loop(), name="agentic-port-events"
        )

    async def stop_port(self) -> None:
        """Stop the event pump and the port."""
        if self._event_task is not None:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None
        if self._port is not None and self._started_port:
            await self._port.stop()
        self._started_port = False
        self._inbound_reply_ref.clear()

    async def _resolve_quote_id(self) -> str | None:
        if self._quote_id_provider is None:
            return None
        result = self._quote_id_provider()
        if inspect.isawaitable(result):
            return await result
        return result

    async def _event_loop(self) -> None:
        try:
            while True:
                event = await self._inbound.get()
                await self._dispatch_event(event)
        except asyncio.CancelledError:
            raise

    async def _dispatch_event(self, event: SubstrateEvent) -> None:
        if isinstance(event, InboundRequest):
            await self._on_inbound_request(event)
        elif isinstance(event, ResponseArrived):
            await self._on_response(event)
        elif isinstance(event, RequestTimedOut):
            await self._on_timeout(event)
        elif isinstance(event, RequestFailed):
            await self._on_failed(event)
        elif isinstance(event, RequestSent):
            return

    async def _on_inbound_request(self, event: InboundRequest) -> None:
        """Serve a locally registered capability (producer path).

        Port-independent (NF-9): when a port is attached the reply goes through
        ``self._port.send_response``; on a port-less forwarder the reply is
        pushed onto the ``queue_to_lower`` response seam instead.
        """
        try:
            parse_capability_name(event.name)
        except CapabilityNamingError:
            # Note: not returning. The wire name a registered producer is
            # reached by is `/cap/fwd/<path>` (submit_intent), which is not a
            # version-marked capability name; registry lookup below decides.
            pass
        reg = self._hub.registry.get(event.name)
        if reg is None:
            return
        reply_ref = self._inbound_reply_ref.pop(event.correlation, None)
        if self._port is None and reply_ref is None:
            self.logger.warning(
                "inbound request %r has no port and no reply ref; dropping",
                event.name,
            )
            return
        try:
            payload = json.loads(event.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        quote_id = await self._resolve_quote_id()
        deadline = event.at + float(reg.descriptor.freshness_bound_s)
        response = await self._hub.invoke(
            event.name, payload, deadline=deadline, quote_id=quote_id
        )
        body: dict[str, Any] = {
            "output_valid": response.output_valid,
            "input_rejected": response.input_rejected,
            "payload": response.payload,
        }
        if response.quote_id is not None:
            body["quote_id"] = response.quote_id
        encoded = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if self._port is not None:
            await self._port.send_response(event.correlation, encoded)
        else:
            assert reply_ref is not None  # checked above
            await self._transmit_response(
                reply_ref=reply_ref, name=event.name, payload=encoded
            )

    async def _transmit_response(
        self, *, reply_ref: int, name: Name, payload: bytes
    ) -> None:
        """Push a response Content onto the forwarder-backed response seam.

        The seam is the layer's own ``queue_to_lower`` — the same queue NF-6's
        downward NFN pass-through consumes — so no adapter internals are
        touched and no new mechanism is introduced (NF-9).

        :param reply_ref: Face id the response must return to.
        :param name: Capability name the inbound request arrived on.
        :param payload: Encoded response body.
        """
        if self.queue_to_lower is None:
            return
        content = Content(PicnName(list(name.components)), payload)
        await self.queue_to_lower.put([reply_ref, content])

    async def _on_response(self, event: ResponseArrived) -> None:
        mapping = self._leaf_index.get(event.correlation)
        if mapping is None:
            return
        parent, leaf_index = mapping
        entry = self._pit.get(parent)
        if entry is None or entry.terminated:
            # A late/duplicate response after the entry terminated: drop
            # idempotently. ContextPIT.record_response raises on a terminated
            # entry, so the guard belongs here (the PIT stays a strict state
            # machine). Mirrors maybe_complete's idempotency.
            return
        digest = hashlib.sha256(event.payload).digest()
        self._pit.record_response(parent, leaf_index, digest)
        await self._maybe_finish(parent, now_ms=int(event.at * 1000))

    async def _on_timeout(self, event: RequestTimedOut) -> None:
        mapping = self._leaf_index.get(event.correlation)
        if mapping is None:
            return
        parent, leaf_index = mapping
        self._pit.record_timeout(parent, leaf_index)
        await self._maybe_finish(parent, now_ms=int(event.at * 1000))

    async def _on_failed(self, event: RequestFailed) -> None:
        # Treat substrate failure like an accountable omission (NULL leaf).
        mapping = self._leaf_index.get(event.correlation)
        if mapping is None:
            return
        parent, leaf_index = mapping
        await self._record_leaf_failure(parent, leaf_index, now_ms=int(event.at * 1000))

    async def _record_leaf_failure(
        self, parent: bytes, leaf_index: int, *, now_ms: int
    ) -> None:
        """Commit an accountable NULL for one leaf and try to finish the intent.

        Reused by substrate-failure events and by TaskGroup leaf failures so a
        crashed/cancelled leaf can never leave ``done`` unresolved. Idempotent
        when the entry already terminated (late duplicate failure).
        """
        entry = self._pit.get(parent)
        if entry is None or entry.terminated:
            return
        self._pit.record_response(parent, leaf_index, NULL_RESPONSE)
        await self._maybe_finish(parent, now_ms=now_ms)

    async def _maybe_finish(self, parent: bytes, *, now_ms: int) -> None:
        completed = maybe_complete(self._pit, parent, now_ms)
        if completed is None:
            return
        fut = self._parent_done.get(parent)
        if fut is not None and not fut.done():
            fut.set_result(completed.trace_root)

    async def submit_intent(
        self,
        *,
        parent_intent_digest: bytes,
        issuer_public_key_der: bytes,
        sub_intents: list[SubIntent],
        aggregation_policy: str = "ALL",
        payload: bytes = b"{}",
        latency_bound: int | None = 1000,
        credential: bytes = b"",
        deadline: float,
        now_ms: int = 0,
        dispatch: str = "serial",
    ) -> bytes:
        """Commit the expected set, forward sub-intents, await aggregation.

        Invariant I2: Context PIT commit happens **before** any ``send_request``.

        :param dispatch: ``\"serial\"`` (default, bit-for-bit legacy order) or
            ``\"concurrent\"``. The concurrent branch runs one TaskGroup leaf
            per sub-intent; a failed/cancelled leaf emits an accountable NULL
            and ``done`` always resolves.
        :return: Final Merkle trace root.
        """
        if self._port is None or not self._started_port:
            raise RuntimeError("start_port() required before submit_intent")
        if dispatch not in ("serial", "concurrent"):
            raise ValueError(f"unknown dispatch: {dispatch!r}")
        port = self._port
        leaf_specs: list[tuple[bytes, bytes, int | None, tuple[str, ...], bytes]] = []
        for sub in sub_intents:
            digest = hashlib.sha256(
                parent_intent_digest
                + bytes(str(sub.index), "ascii")
                + b"/".join(c.encode() if isinstance(c, str) else c for c in sub.capability)
            ).digest()
            leaf_specs.append(
                (
                    digest,
                    payload,
                    latency_bound,
                    tuple(sub.capability),
                    credential,
                )
            )
        entry = self._pit.commit(
            parent_intent_digest=parent_intent_digest,
            issuer_public_key_der=issuer_public_key_der,
            aggregation_policy=aggregation_policy,
            leaf_specs=leaf_specs,
            now_ms=now_ms,
        )
        loop = asyncio.get_running_loop()
        done: asyncio.Future[bytes] = loop.create_future()
        self._parent_done[parent_intent_digest] = done

        # Forward only after commit (I2). Prepass first: every correlation is
        # registered and marked forwarded before any dispatch, so a fast reply
        # can never race an unpopulated leaf mapping.
        sends: list[tuple[Name, bytes, bytes]] = []
        for leaf_index, leaf in enumerate(entry.leaves):
            correlation = hashlib.sha256(
                parent_intent_digest + leaf.sub_intent_digest
            ).digest()
            self._leaf_index[correlation] = (parent_intent_digest, leaf_index)
            self._pit.mark_forwarded(parent_intent_digest, leaf_index)
            # Build a capability-shaped name from path components when possible.
            name = port.name_from_components(
                (b"cap", b"fwd") + tuple(c.encode("utf-8") for c in leaf.capability)
            )
            sends.append((name, leaf.payload, correlation))

        if dispatch == "serial":
            for name, leaf_payload, correlation in sends:
                await port.send_request(
                    name,
                    leaf_payload,
                    correlation=correlation,
                    deadline=deadline,
                )
            return await done

        async def _dispatch_leaf(
            name: Name, leaf_payload: bytes, correlation: bytes
        ) -> None:
            try:
                await port.send_request(
                    name,
                    leaf_payload,
                    correlation=correlation,
                    deadline=deadline,
                )
            except asyncio.CancelledError:
                # Re-cancel after recording an accountable NULL so done resolves.
                parent, leaf_index = self._leaf_index[correlation]
                await self._record_leaf_failure(parent, leaf_index, now_ms=now_ms)
                raise
            except Exception:
                parent, leaf_index = self._leaf_index[correlation]
                await self._record_leaf_failure(parent, leaf_index, now_ms=now_ms)

        async with asyncio.TaskGroup() as tg:
            for name, leaf_payload, correlation in sends:
                tg.create_task(_dispatch_leaf(name, leaf_payload, correlation))
        return await done

    async def data_from_lower(
        self,
        to_lower: asyncio.Queue[Any],
        to_higher: asyncio.Queue[Any],
        data: Any,
    ) -> None:
        """Translate a packet arriving from NFN / the network (producer path).

        Accepts the canonical ``[face_id, Interest]`` shape (face id at ``[0]``)
        and the typed-``Outbound`` inner-item variant. Malformed input returns
        without raising.

        Dispatch policy (NF-7): when the port pump is running, enqueue onto the
        layer-owned ``_inbound`` so port and stack events share one ordered
        pump; on a port-less forwarder the layer's own run loop **is** the pump,
        so dispatch inline via the SAME ``_dispatch_event`` dispatcher.
        """
        item = data.item if isinstance(data, Outbound) else data
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return
        face_id = item[0]
        packet = item[1]
        if not isinstance(face_id, int) or not isinstance(packet, Interest):
            return
        name = Name(tuple(bytes(c) for c in packet.name.components))
        correlation = hashlib.sha256(
            b"inbound:" + b"/".join(name.components)
        ).digest()
        inbound = InboundRequest(
            correlation=correlation,
            name=name,
            payload=b"",
            at=0.0,
        )
        # Reply-face carriage (NF-4 Option 2): side table keyed by correlation.
        self._inbound_reply_ref[correlation] = face_id
        if self._started_port:
            await self._inbound.put(inbound)
        else:
            await self._dispatch_event(inbound)

    async def data_from_higher(
        self,
        to_lower: asyncio.Queue[Any],
        to_higher: asyncio.Queue[Any],
        data: Any,
    ) -> None:
        """Handle intents arriving from the local application.

        Packet-shaped intents are handled in later wiring; port-based
        :meth:`submit_intent` is the supported G.1 entry point.
        """
        _ = (to_lower, to_higher, data)
        return None


__all__ = ["DEFAULT_INBOUND_SIZE", "AgenticLayer"]
