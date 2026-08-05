# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Harness hard-failure errors."""

from __future__ import annotations


class HarnessError(ValueError):
    """Raised when the measurement harness refuses to run or write results."""


__all__ = ["HarnessError"]
