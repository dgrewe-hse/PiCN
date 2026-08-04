"""Run strategies for BasicLinkLayer.

See docs/design-adrs/ADR-008-baseinterface-contract.md's 2026-08-04 addendum
for the full rationale. In short: BasicLinkLayer is production code every
ProgramLib already depends on, so it cannot simply be rewritten in place the
way Phase 2's purely-additive AsyncLayerProcess/AsyncLayerStack were. Instead
BasicLinkLayer takes an injected LinkLayerRunStrategy deciding what actually
happens inside its dedicated forked process:

- SyncRunStrategy reproduces today's LayerProcess.start_process() exactly.
  It is the default, so every existing ProgramLib is completely unaffected.
- AsyncRunStrategy runs a real asyncio engine instead, opt-in per ProgramLib.

Both live in this one module because they implement the same seam and are
meant to be read side by side.
"""

import abc
import asyncio
import concurrent.futures
import multiprocessing
from typing import List

from PiCN.LayerStack.AsyncLayerStack import DEFAULT_QUEUE_SIZE
from PiCN.Layers.LinkLayer.Interfaces import AddressInfo, BaseInterface
from PiCN.Layers.LinkLayer.FaceIDTable import BaseFaceIDTable
from PiCN.Processes.AsyncLayerProcess import AsyncLayerProcess


class LinkLayerRunStrategy(abc.ABC):
    """Decides what BasicLinkLayer.start_process() actually does.

    Injected at BasicLinkLayer construction rather than hardcoded, so a
    ProgramLib can opt into the asyncio-based engine (Task 3.3) without every
    other ProgramLib -- or BasicLinkLayer's public shape -- changing at all.
    """

    @abc.abstractmethod
    def start(self, layer: "BasicLinkLayer") -> None:
        """Start layer's process. Must set layer.process, matching
        PiCNProcess's existing contract (stop_process() calls
        layer.process.terminate())."""


class SyncRunStrategy(LinkLayerRunStrategy):
    """Today's behaviour, extracted verbatim from BasicLinkLayer's inherited
    LayerProcess.start_process() -- select()/poll() multiplexing the
    interfaces' file descriptors alongside queue_from_higher, exactly as
    before this refactor. Zero behavioural change; see Task 3.1's Verify
    step in docs/agent-tasks.md.
    """

    def start(self, layer: "BasicLinkLayer") -> None:
        layer.process = multiprocessing.Process(target=layer._run, args=[
            layer._queue_from_lower, layer._queue_from_higher,
            layer._queue_to_lower, layer._queue_to_higher])
        layer.process.daemon = True
        layer.process.start()


class _LinkLayerEngine(AsyncLayerProcess):
    """The real async link-layer logic, run inside AsyncRunStrategy's forked
    process. Reuses Phase 2's AsyncLayerProcess unmodified for its run()/
    start()/stop() -- this is composition, not a duplicate implementation
    (ADR-008's 2026-08-04 addendum).

    queue_from_lower is fed by the interfaces' register() callbacks (inbound
    network data, ADR-008). queue_from_higher is fed by AsyncRunStrategy's
    bridge task, which drains the real, still-multiprocessing.Queue
    layer.queue_from_higher (there is no AsyncLayerStack yet to hand this
    layer a real asyncio.Queue -- see ADR-009's 2026-08-04 addendum).
    queue_to_higher is set to that real multiprocessing.Queue directly, not
    an asyncio.Queue: data_from_lower dispatches its .put() through the same
    injected executor so the event loop is never the one blocking on it.
    queue_to_lower is unused (None) -- BasicLinkLayer has no lower layer; an
    interface is chosen instead, exactly as the synchronous
    BasicLinkLayer.data_from_higher already documents.
    """

    def __init__(self, interfaces: List[BaseInterface], faceidtable: BaseFaceIDTable,
                 executor: concurrent.futures.Executor, logger_name: str, log_level: int):
        super().__init__(logger_name=logger_name, log_level=log_level)
        self._interfaces = interfaces
        self._faceidtable = faceidtable
        self._executor = executor

    async def data_from_lower(self, to_lower, to_higher, data) -> None:
        """data is (packet, addr, interface_id) as pushed by an interface's
        register() callback. interface_id comes from that push -- it is
        never derived from the interface's identity, per ADR-008."""
        packet, addr, interface_id = data
        addr_info = AddressInfo(addr, interface_id)
        faceid = self._faceidtable.get_or_create_faceid(addr_info)
        self.logger.info("Got data from Network and from Face ID: " + str(faceid) + ", addr: " + str(addr_info.address))
        loop = asyncio.get_running_loop()
        # to_higher is the real multiprocessing.Queue (see class docstring);
        # its .put() is dispatched through the executor rather than called
        # directly, since it is not guaranteed non-blocking (ADR-009).
        await loop.run_in_executor(self._executor, to_higher.put, [faceid, packet])

    async def data_from_higher(self, to_lower, to_higher, data) -> None:
        """to_lower is unused: mirrors BasicLinkLayer.data_from_higher
        (sync), which documents ":param to_lower: None" for the same
        reason."""
        faceid = data[0]
        packet = data[1]
        self.logger.info("Got data from Higher Layer with faceid: " + str(faceid))

        addr_info = self._faceidtable.get_address_info(faceid)
        if not addr_info:
            self.logger.error("No addr_info found for faceid: " + str(faceid))
            return
        try:
            await self._interfaces[addr_info.interface_id].send_async(packet, addr_info.address)
        except Exception:
            # Deliberately "except Exception", NOT a bare "except:" like the
            # synchronous version this mirrors: asyncio.CancelledError is a
            # BaseException, not an Exception, specifically so control-flow
            # cancellation is never accidentally swallowed here (ADR-006
            # rule #2). Everything else about this branch, including the
            # pre-existing int+str concatenation bug in the message below
            # (which would itself raise TypeError if this branch is ever
            # actually reached), is preserved as observed rather than fixed
            # -- see AGENTS.md / ADR-001's characterization rule.
            self.logger.error("Could not sned packet to" + str(addr_info.address) + " Interface with ID" +
                               addr_info.interface_id + " not available")
        self.logger.info("Send packet to: " + str(addr_info.address))


class AsyncRunStrategy(LinkLayerRunStrategy):
    """Runs BasicLinkLayer's logic as real asyncio code (loop.
    create_datagram_endpoint via the interfaces, no select()/file_descriptor
    multiplexing) inside its own forked process. Opt-in per ProgramLib --
    see ADR-008's 2026-08-04 addendum.

    stop_process() is NOT overridden per strategy (BasicLinkLayer.
    stop_process() calls self.process.terminate(), which sends SIGTERM and
    kills this entire forked process outright, exactly as it does for
    SyncRunStrategy today). Nothing here runs a graceful async shutdown path
    in that case -- the try/finally below exists to surface a layer crash
    loudly instead of hanging silently while the process is still alive,
    not to handle stop_process()'s termination path.
    """

    def start(self, layer: "BasicLinkLayer") -> None:
        layer.process = multiprocessing.Process(target=self._entrypoint, args=[layer])
        layer.process.daemon = True
        layer.process.start()

    def _entrypoint(self, layer: "BasicLinkLayer") -> None:
        """Runs inside the freshly-forked child process."""
        asyncio.run(self._async_main(layer))

    async def _async_main(self, layer: "BasicLinkLayer") -> None:
        # Exactly one executor (one pool, one owner -- ADR-009's 2026-08-04
        # addendum does not mandate exactly one THREAD), created at start,
        # not at construction (ADR-009's ordering rule). max_workers must be
        # >1: _bridge_from_higher below occupies one worker PERMANENTLY --
        # it resubmits another indefinitely-blocking
        # layer._queue_from_higher.get() immediately after every item, for
        # as long as this engine runs -- so a pool of exactly 1 would starve
        # data_from_lower's own, per-packet run_in_executor(..., to_higher.put)
        # calls forever. Found by observing inbound packets never arriving
        # despite register() and the queue plumbing both working in
        # isolation: outbound-only traffic (which does not need the
        # executor at all -- send_async() calls the transport directly)
        # worked fine, which is what pointed at the shared pool.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        engine = _LinkLayerEngine(
            layer.interfaces, layer.faceidtable, executor,
            logger_name=layer.logger.name, log_level=layer.logger.level,
        )
        engine.queue_from_lower = asyncio.Queue(maxsize=DEFAULT_QUEUE_SIZE)
        engine.queue_from_higher = asyncio.Queue(maxsize=DEFAULT_QUEUE_SIZE)
        engine.queue_to_higher = layer._queue_to_higher
        engine.queue_to_lower = None

        for index, interface in enumerate(layer.interfaces):
            await interface.register(engine.queue_from_lower, interface_id=index)

        async def _bridge_from_higher() -> None:
            loop = asyncio.get_running_loop()
            while True:
                item = await loop.run_in_executor(executor, layer._queue_from_higher.get)
                await engine.queue_from_higher.put(item)

        bridge_task = asyncio.create_task(_bridge_from_higher(), name="LinkLayer-from_higher-bridge")
        engine_task = engine.start()
        tasks = [bridge_task, engine_task]

        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for t in done:
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc is not None:
                    raise exc
        finally:
            # Ordering per ADR-009: cancel -> await -> shut the executor
            # down, never the other way around. Cancelling an already-done
            # task is a harmless no-op, so this runs unconditionally.
            for t in tasks:
                t.cancel()
            await asyncio.wait(tasks)
            executor.shutdown(wait=True)
