# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""I2 + index-keyed Merkle under concurrent out-of-order completion.

The Context PIT commit must precede every ``RequestSent`` (I2), and the Merkle
trace root must be order-independent: an out-of-order completion (via
``hold_terminals`` + reversed release) yields the identical root as in-order.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.adapters.mock import MockSubstratePort
from agentic.adapters.mock.clock import ManualClock
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.aggregation import recompute_trace_root
from agentic.agentic_layer.decomposer import SubIntent
from agentic.port.events import RequestSent
from agentic.trust import generate_ed25519_private_key
from agentic.trust.merkle import NULL_STEER


def _issuer_der() -> bytes:
    key = generate_ed25519_private_key()
    return key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


def _leaf_digest(parent: bytes, index: int, cap: tuple[str, ...]) -> bytes:
    return hashlib.sha256(
        parent
        + bytes(str(index), "ascii")
        + b"/".join(c.encode() for c in cap)
    ).digest()


class _SentRecorder(MockSubstratePort):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sent_events: list[tuple[int, RequestSent]] = []

    async def _put(self, event):
        if isinstance(event, RequestSent):
            self.sent_events.append((len(self.sent_events), event))
        await super()._put(event)


class _HoldingPort(_SentRecorder):
    """Hold terminals so out-of-order release can be exercised."""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("hold_terminals", True)
        super().__init__(**kwargs)


@pytest.mark.asyncio
async def test_i2_commit_precedes_all_requests_sent() -> None:
    port = _HoldingPort(clock=ManualClock(0.0))
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        parent = hashlib.sha256(b"i2-concurrent").digest()
        subs = [SubIntent(index=i, capability=("leaf", f"c{i}"), bindings={}) for i in range(3)]
        task = asyncio.create_task(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=_issuer_der(),
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
                dispatch="concurrent",
            )
        )
        await asyncio.sleep(0.05)
        # All RequestSent events delivered; entry committed and marked forwarded.
        entry = layer.pit.get(parent)
        assert entry is not None and entry.committed
        assert len(port.sent_events) == 3
        assert all(leaf.forwarded for leaf in entry.leaves)
        # Release all so the task completes.
        await port.release_terminals(port.buffered_correlations())
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        await layer.stop_port()


@pytest.mark.asyncio
async def test_out_of_order_release_identical_root() -> None:
    """Reversed release order yields the same trace root as in-order."""
    parent = hashlib.sha256(b"ooo").digest()
    subs = [SubIntent(index=i, capability=("leaf", f"c{i}"), bindings={}) for i in range(4)]

    async def run_one(order_reversed: bool) -> bytes:
        port = _HoldingPort(clock=ManualClock(0.0))
        layer = AgenticLayer(runtime="async", port=port)
        await layer.start_port()
        try:
            corrs: list[bytes] = []
            for sub in subs:
                d = _leaf_digest(parent, sub.index, sub.capability)
                corr = hashlib.sha256(parent + d).digest()
                corrs.append(corr)
                port.script_response(corr, f"resp-{sub.index}".encode())
            task = asyncio.create_task(
                layer.submit_intent(
                    parent_intent_digest=parent,
                    issuer_public_key_der=_issuer_der(),
                    sub_intents=subs,
                    aggregation_policy="ALL",
                    deadline=10.0,
                    now_ms=0,
                    dispatch="concurrent",
                )
            )
            await asyncio.sleep(0.05)
            order = list(reversed(corrs)) if order_reversed else list(corrs)
            await port.release_terminals(order)
            root = await asyncio.wait_for(task, timeout=5.0)
            return root
        finally:
            await layer.stop_port()

    in_order = await run_one(order_reversed=False)
    reversed_order = await run_one(order_reversed=True)
    assert in_order == reversed_order


@pytest.mark.asyncio
async def test_concurrent_root_matches_recompute() -> None:
    """The concurrent root matches an independent recomputation (order-free)."""
    parent = hashlib.sha256(b"recompute").digest()
    subs = [SubIntent(index=i, capability=("leaf", f"c{i}"), bindings={}) for i in range(3)]
    port = _HoldingPort(clock=ManualClock(0.0))
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        for sub in subs:
            d = _leaf_digest(parent, sub.index, sub.capability)
            corr = hashlib.sha256(parent + d).digest()
            port.script_response(corr, f"resp-{sub.index}".encode())
        task = asyncio.create_task(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=_issuer_der(),
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
                dispatch="concurrent",
            )
        )
        await asyncio.sleep(0.05)
        # Release out of order to prove order-independence.
        await port.release_terminals(list(reversed(port.buffered_correlations())))
        root = await asyncio.wait_for(task, timeout=5.0)

        ordered = sorted(
            _leaf_digest(parent, sub.index, sub.capability) for sub in subs
        )
        responses = [
            hashlib.sha256(f"resp-{sub.index}".encode()).digest()
            for sub in sorted(subs, key=lambda s: _leaf_digest(parent, s.index, s.capability))
        ]
        expected = recompute_trace_root(
            tuple(ordered),
            tuple([NULL_STEER] * len(ordered)),
            tuple(responses),
        )
        assert root == expected
    finally:
        await layer.stop_port()