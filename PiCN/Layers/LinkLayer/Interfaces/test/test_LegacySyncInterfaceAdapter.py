"""Test LegacySyncInterfaceAdapter (Task 3.4). Plain pytest module, not
unittest.TestCase -- see ADR-010's Addendum.
"""

import asyncio
import concurrent.futures

import pytest

from PiCN.Layers.LinkLayer.Interfaces import BaseInterface, LegacySyncInterfaceAdapter


class _FakeSyncInterface(BaseInterface):
    """A minimal, in-memory, old-style (blocking) BaseInterface -- no real
    socket required.

    receive() busy-polls a plain list, but MUST remain boundedly responsive
    to close(): cancelling the asyncio task awaiting it (as
    LegacySyncInterfaceAdapter.close() does) does not interrupt the executor
    thread actually running it -- ADR-009 is explicit that cancellation
    never stops a thread mid-flight. A receive() that loops forever with no
    exit condition would hang ThreadPoolExecutor.shutdown(wait=True) in
    teardown forever; checking self._closed every iteration is what makes
    this fake a *safe* executor function rather than a leaked, immortal
    thread.
    """

    def __init__(self):
        self.sent = []
        self._inbox: list = []
        self._closed = False

    def send(self, data, addr):
        self.sent.append((data, addr))

    def receive(self):
        import time
        while not self._inbox and not self._closed:
            time.sleep(0.01)
        if self._closed and not self._inbox:
            raise OSError("_FakeSyncInterface closed while awaiting receive()")
        return self._inbox.pop(0)

    def push_inbound(self, data, addr):
        self._inbox.append((data, addr))

    def close(self):
        self._closed = True

    @property
    def file_descriptor(self):
        raise NotImplementedError("not used by this fake")


class TestLegacySyncInterfaceAdapter:
    """Test LegacySyncInterfaceAdapter"""

    def setup_method(self):
        self.fake = _FakeSyncInterface()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    def teardown_method(self):
        self.executor.shutdown(wait=True)

    def test_construction_emits_a_deprecation_warning(self):
        with pytest.warns(DeprecationWarning):
            LegacySyncInterfaceAdapter(self.fake, self.executor)

    @pytest.mark.asyncio
    async def test_send_async_delegates_to_the_wrapped_interfaces_blocking_send(self):
        with pytest.warns(DeprecationWarning):
            adapter = LegacySyncInterfaceAdapter(self.fake, self.executor)

        await adapter.send_async(b"HelloWorld", ("127.0.0.1", 1234))

        assert self.fake.sent == [(b"HelloWorld", ("127.0.0.1", 1234))]

    @pytest.mark.asyncio
    async def test_register_bridges_the_wrapped_interfaces_blocking_receive_onto_the_queue(self):
        with pytest.warns(DeprecationWarning):
            adapter = LegacySyncInterfaceAdapter(self.fake, self.executor)

        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        await adapter.register(queue, interface_id=3)

        self.fake.push_inbound(b"HelloWorld", ("127.0.0.1", 4321))

        data, addr, interface_id = await asyncio.wait_for(queue.get(), timeout=2.0)
        adapter.close()

        assert data == b"HelloWorld"
        assert addr == ("127.0.0.1", 4321)
        assert interface_id == 3

    @pytest.mark.asyncio
    async def test_send_receive_round_trip_through_the_adapter(self):
        """send()/receive() round-trip through the adapter, matching Task
        3.4's Verify prompt wording."""
        with pytest.warns(DeprecationWarning):
            adapter = LegacySyncInterfaceAdapter(self.fake, self.executor)

        await adapter.send_async(b"round-trip", ("127.0.0.1", 1))
        self.fake.push_inbound(*self.fake.sent[0])

        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        await adapter.register(queue, interface_id=0)
        data, addr, _interface_id = await asyncio.wait_for(queue.get(), timeout=2.0)
        adapter.close()

        assert data == b"round-trip"
        assert addr == ("127.0.0.1", 1)

    def test_file_descriptor_raises(self):
        with pytest.warns(DeprecationWarning):
            adapter = LegacySyncInterfaceAdapter(self.fake, self.executor)

        with pytest.raises(NotImplementedError):
            _ = adapter.file_descriptor
