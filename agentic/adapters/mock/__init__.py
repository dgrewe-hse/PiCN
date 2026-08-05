# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""In-memory substrate adapter for unit and integration tests.

Provides deterministic delivery, injectable failures, and controllable
response ordering without a network or a stack adapter.
"""

from agentic.adapters.mock.clock import Clock, ManualClock
from agentic.adapters.mock.port import MockSubstratePort

__all__ = [
    "Clock",
    "ManualClock",
    "MockSubstratePort",
]
