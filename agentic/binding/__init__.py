# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Capability backends: deterministic functions and agent frameworks.

LLM client libraries (for example ``pydantic_ai``) may appear only in this
package. The forwarding path must not import them. Registration checks that a
backend conforms to the capability descriptor; the descriptor stays
authoritative.
"""

from agentic.binding.deterministic import DeterministicBackend
from agentic.binding.protocol import CapabilityBackend
from agentic.binding.registry import (
    BindingResponse,
    CapabilityRegistration,
    CapabilityRegistry,
)
from agentic.binding.schema import (
    SchemaCompatibilityError,
    SchemaProfileError,
    assert_restricted_profile,
    schemas_compatible,
    validate_against_schema,
)

__all__ = [
    "BindingResponse",
    "CapabilityBackend",
    "CapabilityRegistration",
    "CapabilityRegistry",
    "DeterministicBackend",
    "SchemaCompatibilityError",
    "SchemaProfileError",
    "assert_restricted_profile",
    "schemas_compatible",
    "validate_against_schema",
]

# PydanticAIBackend is imported from agentic.binding.pydantic_ai_backend so
# AC3 stays intact: agentic_layer never pulls pydantic_ai via this package root.

