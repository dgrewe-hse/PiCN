# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Forwarder adapters that implement the substrate port.

``picn`` is the only submodule allowed to import ``PiCN.*``. ``mock`` is an
in-memory adapter for tests. Agentic mechanisms must depend on the port
interface, not on adapter internals.
"""
