# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Async-only agentic layer: capability FIB, decomposer, context PIT.

Sits above NFN in the stack. New code with no sync callers — there is no
sync wrapper and no shared ``*Core`` split. Imports the substrate port, never
adapter internals, and never an LLM client.

Stack wiring imports ``PiCN.Processes`` / ``PiCN.Packets`` only; all other
``PiCN.*`` imports stay in ``agentic.adapters.picn``.
"""

from agentic.agentic_layer.cfib import CapabilityFIB, MatchConstraints
from agentic.agentic_layer.descriptor import CapabilityDescriptor, register_descriptor
from agentic.agentic_layer.layer import AgenticLayer
from agentic.agentic_layer.naming import (
    CapabilityNamingError,
    ParsedCapabilityName,
    capability_lpm_prefix,
    capability_name,
    issuer_digest_from_key,
    issuer_digest_matches,
    parse_capability_name,
)
from agentic.agentic_layer.runtime import SyncRuntimeNotSupported, require_async_runtime

__all__ = [
    "AgenticLayer",
    "CapabilityDescriptor",
    "CapabilityFIB",
    "CapabilityNamingError",
    "MatchConstraints",
    "ParsedCapabilityName",
    "SyncRuntimeNotSupported",
    "capability_lpm_prefix",
    "capability_name",
    "issuer_digest_from_key",
    "issuer_digest_matches",
    "parse_capability_name",
    "register_descriptor",
    "require_async_runtime",
]
