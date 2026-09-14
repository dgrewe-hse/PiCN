# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Dispatch mode for :meth:`~agentic.agentic_layer.layer.AgenticLayer.submit_intent`.

The enum formalises the two fan-out paths measured by Experiment D: serial
(legacy, bit-for-bit order) and concurrent (``asyncio.TaskGroup``). The string
values are the ``submit_intent`` ``dispatch`` parameter values, kept for
backward compatibility with callers that pass the raw string.
"""

from __future__ import annotations

from enum import Enum


class DispatchMode(str, Enum):
    """Fan-out dispatch strategy for ``submit_intent``.

    :cvar SERIAL: Legacy ordered fan-out (one ``await`` per leaf, in index order).
    :cvar CONCURRENT: ``asyncio.TaskGroup`` fan-out (all sends scheduled first).
    """

    SERIAL = "serial"
    CONCURRENT = "concurrent"

    @classmethod
    def coerce(cls, value: str | "DispatchMode") -> "DispatchMode":
        """Coerce a string or enum value to a :class:`DispatchMode`.

        :param value: ``"serial"``/``"concurrent"`` or an existing enum member.
        :return: The normalised enum member.
        :raises ValueError: For an unknown mode string.
        """
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"unknown dispatch: {value!r}") from exc


__all__ = ["DispatchMode"]