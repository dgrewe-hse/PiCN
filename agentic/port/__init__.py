# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Substrate port: typed Protocol and lifecycle events.

Defines the narrow boundary between the agentic layer and any underlying
forwarder. No ``PiCN`` types belong here — adapters translate at the edges.
"""
