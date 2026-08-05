# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Cardiac emergency-response scenario plugin.

Transport-agnostic orchestration of ambulance intent, hospital capacity
queries (including an adversarial inflated claim with a *valid* quote),
traffic ETA, edge ranking, accountability / outcome logs, and KP-A
cross-verification. The harness injects the substrate port; this package
never branches on bus versus UDP.
"""

from agentic.scenario.cardiac.scenario import CardiacScenario

__all__ = ["CardiacScenario"]
