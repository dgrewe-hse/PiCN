# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Late/duplicate ResponseArrived after termination is dropped idempotently.

``ContextPIT.record_response`` raises ``ValueError`` on a terminated entry; the
guard lives in ``AgenticLayer._on_response`` so the PIT stays a strict state
machine while the layer tolerates late duplicates.
"""

from __future__ import annotations

import asyncio
import hashlib
import time

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.adapters.mock import MockSubstratePort
from agentic.adapters.mock.clock import ManualClock
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.decomposer import SubIntent
from agentic.port.events import ResponseArrived
from agentic.port.names import Name
from agentic.trust import generate_ed25519_private_key


def _issuer_der() -> bytes:
    key = generate_ed25519_private_key()
    return key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


@pytest.mark.asyncio
async def test_duplicate_response_after_termination_is_dropped() -> None:
    clock = ManualClock(0.0)
    port = MockSubstratePort(clock=clock)
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        parent = hashlib.sha256(b"late").digest()
        subs = [SubIntent(index=0, capability=("x",), bindings={})]
        digest = hashlib.sha256(parent + b"0" + b"x").digest()
        corr = hashlib.sha256(parent + digest).digest()
        port.script_response(corr, b"ok")

        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=_issuer_der(),
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
            ),
            timeout=2.0,
        )
        entry = layer.pit.get(parent)
        assert entry is not None and entry.terminated
        first_root = entry.trace_root
        assert root == first_root

        # Inject a duplicate/late response for the same correlation.
        await layer._on_response(
            ResponseArrived(correlation=corr, name=port.name_from_components([b"x"]), payload=b"late", at=time.monotonic())
        )
        # Re-inject directly to prove no ValueError even if the event pump is idle.
        await layer._dispatch_event(
            ResponseArrived(correlation=corr, name=port.name_from_components([b"x"]), payload=b"late", at=time.monotonic())
        )
        entry2 = layer.pit.get(parent)
        assert entry2 is not None
        assert entry2.trace_root == first_root
    finally:
        await layer.stop_port()


@pytest.mark.asyncio
async def test_unknown_correlation_response_is_dropped() -> None:
    layer = AgenticLayer(runtime="async")
    await layer._on_response(
        ResponseArrived(
            correlation=b"\xff" * 32,
            name=Name((b"x",)),
            payload=b"z",
            at=0.0,
        )
    )