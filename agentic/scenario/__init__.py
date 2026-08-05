# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Pluggable scenario framework.

Concrete demos (for example cardiac response) live as **scenario plugins**
under this package. The harness and agentic layer depend only on
:class:`Scenario` — swap or add scenarios without forking the stack.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Scenario(Protocol):
    """Transport-agnostic scenario contract.

    Implementations own world state, actors, and assertions. They must not
    branch on transport (``bus`` vs ``udp``); the harness injects the port.
    """

    @property
    def name(self) -> str:
        """Stable scenario identifier (used in run metadata)."""
        ...

    async def setup(self, **kwargs: Any) -> None:
        """Create actors / world state before a run."""
        ...

    async def run_happy_path(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the successful path; return structured run artefacts."""
        ...

    async def run_adversary(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the adversary sub-case when the scenario defines one."""
        ...

    async def teardown(self) -> None:
        """Release resources after a run."""
        ...


def available_scenarios() -> dict[str, type]:
    """Return registered scenario classes keyed by ``name``.

    Populated as scenario plugins are imported (cardiac, …).
    """
    return dict(_REGISTRY)


_REGISTRY: dict[str, type] = {}


def register_scenario(cls: type) -> type:
    """Class decorator to publish a scenario plugin."""
    name = getattr(cls, "scenario_name", None) or getattr(cls, "name", None)
    if not isinstance(name, str) or not name:
        # Allow instances to expose ``name``; classes may set ``scenario_name``.
        scenario_name = getattr(cls, "scenario_name", "")
        if not isinstance(scenario_name, str) or not scenario_name:
            raise TypeError("scenario class needs scenario_name: str")
        name = scenario_name
    _REGISTRY[name] = cls
    return cls


__all__ = [
    "Scenario",
    "available_scenarios",
    "register_scenario",
]
