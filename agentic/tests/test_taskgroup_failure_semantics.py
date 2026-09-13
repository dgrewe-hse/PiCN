# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Deterministic TaskGroup failure semantics (NF-3 hardening).

A leaf whose ``send_request`` raises (or is cancelled) must still produce an
accountable NULL leaf, and ``done`` must always resolve — never hang.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.adapters.mock import MockSubstratePort
from agentic.adapters.mock.clock import ManualClock
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.decomposer import SubIntent
from agentic.trust import generate_ed25519_private_key
from agentic.trust.merkle import NULL_RESPONSE


class _ExplodingPort(MockSubstratePort):
    """Fails ``send_request`` for a chosen capability name substring."""

    def __init__(self, *, explode_substring: bytes, **kwargs) -> None:
        super().__init__(**kwargs)
        self._explode = explode_substring
        self.sent: list[bytes] = []

    async def send_request(self, name, payload, *, correlation, deadline) -> None:
        self.sent.append(b"/".join(name.components))
        if self._explode in b"/".join(name.components):
            raise RuntimeError("boom")
        await super().send_request(name, payload, correlation=correlation, deadline=deadline)


def _issuer_der() -> bytes:
    key = generate_ed25519_private_key()
    return key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


@pytest.mark.asyncio
async def test_one_leaf_raises_emits_null_and_resolves() -> None:
    clock = ManualClock(0.0)
    port = _ExplodingPort(explode_substring=b"boom", clock=clock)
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        parent = hashlib.sha256(b"tg-fail").digest()
        subs = [
            SubIntent(index=0, capability=("ok",), bindings={}),
            SubIntent(index=1, capability=("boom",), bindings={}),
        ]
        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=_issuer_der(),
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
                dispatch="concurrent",
            ),
            timeout=2.0,
        )
        entry = layer.pit.get(parent)
        assert entry is not None and entry.terminated
        assert root == entry.trace_root
        nulls = [leaf for leaf in entry.leaves if leaf.response_digest == NULL_RESPONSE]
        assert len(nulls) == 1
        assert nulls[0].capability == ("boom",)
    finally:
        await layer.stop_port()


@pytest.mark.asyncio
async def test_all_leaves_raise_still_resolves() -> None:
    clock = ManualClock(0.0)
    port = _ExplodingPort(explode_substring=b"boom", clock=clock)
    layer = AgenticLayer(runtime="async", port=port)
    await layer.start_port()
    try:
        parent = hashlib.sha256(b"tg-fail-all").digest()
        subs = [SubIntent(index=0, capability=("boom",), bindings={})]
        root = await asyncio.wait_for(
            layer.submit_intent(
                parent_intent_digest=parent,
                issuer_public_key_der=_issuer_der(),
                sub_intents=subs,
                aggregation_policy="ALL",
                deadline=10.0,
                now_ms=0,
                dispatch="concurrent",
            ),
            timeout=2.0,
        )
        entry = layer.pit.get(parent)
        assert entry is not None and entry.terminated
        assert entry.leaves[0].response_digest == NULL_RESPONSE
        assert root == entry.trace_root
    finally:
        await layer.stop_port()