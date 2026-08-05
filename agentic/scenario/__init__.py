# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""End-to-end scenario actors and world-state simulation.

Transport-agnostic: scenario code must not branch on bus versus UDP.
Must not import ``PiCN.*``.
"""
