# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Bus executor-capacity guard (A-011)."""

from __future__ import annotations

from agentic.benchmark.errors import HarnessError


def assert_bus_capacity(
    *,
    transport: str,
    executor_workers: int,
    simulated_interfaces: int,
) -> None:
    """Refuse a bus run when interfaces would saturate the shared executor.

    :raises HarnessError: When ``transport==\"bus\"`` and
        ``executor_workers <= simulated_interfaces``, naming A-011.
    """
    if transport != "bus":
        return
    if executor_workers > simulated_interfaces:
        return
    raise HarnessError(
        "A-011: refusing bus run — executor_workers "
        f"({executor_workers}) must be greater than simulated_interfaces "
        f"({simulated_interfaces}); do not silently raise the worker count"
    )


__all__ = ["assert_bus_capacity"]
