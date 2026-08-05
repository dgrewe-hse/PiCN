# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Async-only agentic layer: capability FIB, decomposer, context PIT.

Sits above NFN in the stack. New code with no sync callers — there is no
sync wrapper and no shared ``*Core`` split. Imports the substrate port, never
adapter internals, and never an LLM client.
"""
