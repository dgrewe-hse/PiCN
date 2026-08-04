"""Test the abstract async layer base class AsyncLayerProcess

NOTE: this file deliberately does NOT use unittest.TestCase, unlike most of
the suite. pytest-asyncio's @pytest.mark.asyncio has no effect on a coroutine
method defined on a unittest.TestCase subclass -- pytest hands those to
unittest's own runner, which calls the coroutine, discards the resulting
coroutine object without awaiting it, and reports the test as passed without
ever running its body. See docs/design-adrs/ADR-010-async-test-strategy.md,
"Addendum". Do not change this file back to unittest.TestCase.
"""

import asyncio

import pytest

from PiCN.Processes.AsyncLayerProcess import AsyncLayerProcess


class AsyncLayerMock(AsyncLayerProcess):
    """Mock implementation of an AsyncLayerProcess: mirrors LayerMock in
    test_LayerProcess.py, crossing the data over from one side to the other.
    """

    def __init__(self):
        super().__init__()

    async def data_from_lower(self, to_lower: asyncio.Queue, to_higher: asyncio.Queue, data) -> None:
        await to_higher.put(data)

    async def data_from_higher(self, to_lower: asyncio.Queue, to_higher: asyncio.Queue, data) -> None:
        await to_lower.put(data)


class TestAsyncLayerProcess:
    """Test the abstract async layer base class AsyncLayerProcess.

    Named with a capital T (unlike this repo's usual test_ClassName
    convention): pytest only auto-collects bare classes matching its default
    python_classes pattern ("Test*"). unittest.TestCase subclasses are exempt
    from that pattern -- pytest discovers them via inheritance instead -- which
    is why the rest of the suite gets away with lowercase names. A bare class
    has no such exemption.
    """

    def setup_method(self):
        self.q_from_higher: asyncio.Queue = asyncio.Queue()
        self.q_from_lower: asyncio.Queue = asyncio.Queue()
        self.q_to_higher: asyncio.Queue = asyncio.Queue()
        self.q_to_lower: asyncio.Queue = asyncio.Queue()

        self.layer: AsyncLayerMock = AsyncLayerMock()
        self.layer.queue_from_higher = self.q_from_higher
        self.layer.queue_from_lower = self.q_from_lower
        self.layer.queue_to_higher = self.q_to_higher
        self.layer.queue_to_lower = self.q_to_lower

    @pytest.mark.asyncio
    async def test_from_higher_to_lower(self):
        """Test correct handling from Higher to Lower"""
        self.layer.start()
        try:
            await self.q_from_higher.put("Testdata")
            output = await asyncio.wait_for(self.q_to_lower.get(), timeout=1)
            assert output == "Testdata"
        finally:
            await self.layer.stop()

    @pytest.mark.asyncio
    async def test_from_lower_to_higher(self):
        """Test correct handling from Lower to Higher"""
        self.layer.start()
        try:
            await self.q_from_lower.put("Testdata")
            output = await asyncio.wait_for(self.q_to_higher.get(), timeout=1)
            assert output == "Testdata"
        finally:
            await self.layer.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_a_noop(self):
        """ADR-006, rule #4: stop() on a layer that never started must not raise."""
        await self.layer.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        """Calling start() again while already running returns the SAME task
        rather than creating a second run loop.
        """
        task_one = self.layer.start()
        task_two = self.layer.start()
        try:
            assert task_one is task_two
        finally:
            await self.layer.stop()

    @pytest.mark.asyncio
    async def test_stop_actually_stops_the_loop(self):
        """After stop() returns, the task is done and no further item placed
        on a queue is ever processed.
        """
        task = self.layer.start()
        await self.q_from_lower.put("before-stop")
        await asyncio.wait_for(self.q_to_higher.get(), timeout=1)

        await self.layer.stop()
        assert task.done()

        await self.q_from_lower.put("after-stop")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(self.q_to_higher.get(), timeout=0.2)
