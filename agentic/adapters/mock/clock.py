# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Injectable clocks for deterministic mock-adapter tests."""

from __future__ import annotations

from collections.abc import Callable


class ManualClock:
    """Monotonic clock the test advances explicitly.

    Never call wall-clock APIs inside the mock path — inject this (or any
    ``Callable[[], float]``) so deadlines and event timestamps are reproducible.

    :param start: Initial time value.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        """Move the clock forward by ``delta`` (must be non-negative).

        :param delta: Seconds to add.
        :raises ValueError: If ``delta`` is negative.
        """
        if delta < 0:
            raise ValueError(f"clock cannot move backwards (delta={delta})")
        self._now += delta

    def set(self, value: float) -> None:
        """Jump to an absolute time (tests only; prefer :meth:`advance`).

        :param value: New absolute time.
        """
        self._now = value


Clock = Callable[[], float]
