# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution under the BSD 3-Clause License; see the LICENSE file for text.

"""Serial dispatch path is unchanged by PR-1 (golden).

Serial is the default and must stay bit-for-bit: one ``send_request`` per leaf,
incommitted-then-forwarded order, and a trace root equal to an independent
recomputation over the committed leaf specs and the responses.
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
from agentic.trust import generate_ed25519_private_key
from agentic.trust.merkle import NULL_STEER, leaf_digest


class _RecordingPort(MockSubstratePort):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.dispatch_order: list[bytes] = []

    async def send_request(self, name, payload, *, correlation, deadline) -> None:
        self.dispatch_order.append(b"/".join(name.components))
        await super().send_request(name, payload, correlation=correlation, deadline=deadline)


def _issuer_der() -> bytes:
    key = generate_ed25519_private_key()
    return key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


@pytest.mark.asyncio
async def test_serial_dispatch_golden() -> None:
    clock = ManualClock(0.0)
    port = _RecordingPort(clock=clock)
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        parent = hashlib.sha256(b"serial-golden").digest()
        subs = [
            SubIntent(index=0, capability=("hospital", "beds"), bindings={}),
            SubIntent(index=1, capability=("traffic", "eta"), bindings={}),
        ]
        expected_digests = []
        for sub in subs:
            digest = hashlib.sha256(
                parent
                + bytes(str(sub.index), "ascii")
                + b"/".join(c.encode() for c in sub.capability)
            ).digest()
            expected_digests.append(digest)
            corr = hashlib.sha256(parent + digest).digest()
            port.script_response(corr, f"ok-{sub.index}".encode())

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
        # One dispatch per leaf, in index order.
        assert len(port.dispatch_order) == 2
        assert port.dispatch_order[0].endswith(b"hospital/beds")
        assert port.dispatch_order[1].endswith(b"traffic/eta")

        # Root matches an independent recomputation over spec-ordered leaves.
        ordered = sorted(expected_digests)
        responses = []
        for digest in ordered:
            for sub in subs:
                d = hashlib.sha256(
                    parent + bytes(str(sub.index), "ascii") + b"/".join(c.encode() for c in sub.capability)
                ).digest()
                if d == digest:
                    responses.append(hashlib.sha256(f"ok-{sub.index}".encode()).digest())
        expected_root = recompute_trace_root(
            tuple(ordered),
            tuple([NULL_STEER] * len(ordered)),
            tuple(responses),
        )
        assert root == expected_root
    finally:
        await layer.stop_port()