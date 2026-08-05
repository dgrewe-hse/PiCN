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
