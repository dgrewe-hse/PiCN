# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Measurement harness: sweeps, metrics, and rollups.

Must not import ``PiCN.*``. Runs refuse to start without a seed, without
transport metadata, or from a dirty working tree (unless explicitly allowed).
"""
