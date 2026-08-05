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
from agentic.agentic_layer.context_pit import (
    MAX_CONTEXT_PIT_ENTRIES,
    RESULT_RETENTION_MS,
    ContextPIT,
    ContextPitEntry,
    ContextPitLeaf,
)
from agentic.agentic_layer.decomposer import (
    BoundedDecomposer,
    SubIntent,
    TaskGraphTemplate,
    expected_set_from,
    load_template,
)
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
from agentic.agentic_layer.steer import (
    MAX_STEERS_PER_SUBINTENT,
    NULL_STEER,
    AgenticSteer,
    SteerRejected,
    append_steer_chain,
    apply_steer,
    build_steer_body,
    sign_steer,
)

__all__ = [
    "MAX_CONTEXT_PIT_ENTRIES",
    "MAX_STEERS_PER_SUBINTENT",
    "NULL_STEER",
    "RESULT_RETENTION_MS",
    "AgenticLayer",
    "AgenticSteer",
    "BoundedDecomposer",
    "CapabilityDescriptor",
    "CapabilityFIB",
    "CapabilityNamingError",
    "ContextPIT",
    "ContextPitEntry",
    "ContextPitLeaf",
    "MatchConstraints",
    "ParsedCapabilityName",
    "SteerRejected",
    "SubIntent",
    "SyncRuntimeNotSupported",
    "TaskGraphTemplate",
    "append_steer_chain",
    "apply_steer",
    "build_steer_body",
    "capability_lpm_prefix",
    "capability_name",
    "expected_set_from",
    "issuer_digest_from_key",
    "issuer_digest_matches",
    "load_template",
    "parse_capability_name",
    "register_descriptor",
    "require_async_runtime",
    "sign_steer",
]
