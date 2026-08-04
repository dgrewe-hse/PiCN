"""Bridges an old-style (blocking receive(), synchronous send())
BaseInterface implementation so it can be driven by AsyncRunStrategy.

See docs/design-adrs/ADR-008-baseinterface-contract.md's "Migration path for
third-party implementations" section. This is a migration aid for interfaces
that have not adopted the register()/send_async() contract, not a supported
long-term path -- hence the DeprecationWarning raised on construction.
"""

import asyncio
import concurrent.futures
import warnings
from typing import Optional

from PiCN.Layers.LinkLayer.Interfaces import BaseInterface


class LegacySyncInterfaceAdapter(BaseInterface):
    """Wraps an old-style BaseInterface (blocking receive(), synchronous
    send()) so it can be registered with an AsyncRunStrategy-driven engine
    exactly like a native async interface.

    DEPRECATED on introduction (ADR-008): this exists so a third party's
    un-migrated interface keeps working, at the cost of one executor thread
    per wrapped interface for as long as it is used this way.
    """

    def __init__(self, wrapped: BaseInterface, executor: concurrent.futures.Executor):
        """
        :param wrapped: the old-style interface to bridge. Its blocking
            receive()/send() are used exactly as they are -- neither is
            modified.
        :param executor: injected, never created here (ADR-009 rule #3 and
            its 2026-08-04 addendum -- this adapter is not an owner).
        """
        warnings.warn(
            "LegacySyncInterfaceAdapter is a migration aid for BaseInterface "
            "implementations that have not adopted register()/send_async() "
            "(ADR-008). It is not a supported long-term path.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._wrapped = wrapped
        self._executor = executor
        self._receive_task: Optional[asyncio.Task] = None

    async def register(self, queue: asyncio.Queue, interface_id: int) -> None:
        """Starts a background task that repeatedly calls the wrapped
        interface's blocking receive() in the injected executor, pushing
        each result onto queue as (data, addr, interface_id).

        Unlike UDP4Interface's datagram_received() (Task 3.2), this runs as
        a real task, not a non-coroutine callback, so it CAN await
        queue.put(...) for real backpressure (ADR-005) -- there is no
        callback constraint here forcing the put_nowait()-and-drop exception
        UDP4Interface needed.
        """
        loop = asyncio.get_running_loop()

        async def _pump() -> None:
            while True:
                data, addr = await loop.run_in_executor(self._executor, self._wrapped.receive)
                await queue.put((data, addr, interface_id))

        self._receive_task = asyncio.create_task(_pump(), name=f"LegacySyncInterfaceAdapter-{interface_id}")

    async def send_async(self, data, addr) -> None:
        """Dispatches the wrapped interface's blocking send() to the
        injected executor rather than calling it directly on the event loop
        thread -- the wrapped implementation offers no guarantee it will not
        block (ADR-008/ADR-009)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._wrapped.send, data, addr)

    def send(self, data, addr):
        """Synchronous send(), unused by AsyncRunStrategy, kept only because
        BaseInterface still declares it. Delegates to the wrapped interface
        unchanged."""
        self._wrapped.send(data, addr)

    def receive(self):
        """Synchronous receive(), unused by AsyncRunStrategy (register()
        drives the wrapped interface itself). Delegates unchanged."""
        return self._wrapped.receive()

    @property
    def file_descriptor(self):
        raise NotImplementedError(
            "file_descriptor is not supported by LegacySyncInterfaceAdapter; "
            "interfaces driven by AsyncRunStrategy do not multiplex file "
            "descriptors. See docs/design-adrs/ADR-008-baseinterface-contract.md."
        )

    def enable_broadcast(self) -> bool:
        return self._wrapped.enable_broadcast()

    def get_broadcast_address(self) -> str:
        return self._wrapped.get_broadcast_address()

    def close(self):
        if self._receive_task is not None:
            self._receive_task.cancel()
        wrapped_close = getattr(self._wrapped, "close", None)
        if wrapped_close is not None:
            wrapped_close()
