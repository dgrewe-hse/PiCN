# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Agentic capability-routing package.

This top-level package is substrate-agnostic: only
``agentic.adapters.picn`` may import ``PiCN.*``. Trust, scenario, benchmark,
and the port interface stay free of stack types so the layer can later run on
another forwarder. The forwarding path never imports an LLM client; agent
frameworks live under ``agentic.binding`` only.

Architectural import contracts are enforced by
``agentic.tests.test_architecture`` and summarised in ``agentic/README.md``.
"""
