# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""``DispatchMode``: the fan-out dispatch strategy enum for ``submit_intent``."""

from __future__ import annotations

import pytest

from agentic.agentic_layer.dispatch import DispatchMode


def test_coerce_string_serial() -> None:
    assert DispatchMode.coerce("serial") is DispatchMode.SERIAL


def test_coerce_string_concurrent() -> None:
    assert DispatchMode.coerce("concurrent") is DispatchMode.CONCURRENT


def test_coerce_enum_passthrough() -> None:
    """Passing an existing member returns it unchanged (the isinstance branch)."""
    assert DispatchMode.coerce(DispatchMode.SERIAL) is DispatchMode.SERIAL
    assert DispatchMode.coerce(DispatchMode.CONCURRENT) is DispatchMode.CONCURRENT


def test_coerce_unknown_raises() -> None:
    with pytest.raises(ValueError):
        DispatchMode.coerce("bogus")


def test_enum_is_str_subclass() -> None:
    """String values match the ``submit_intent`` dispatch parameter values."""
    assert DispatchMode.SERIAL.value == "serial"
    assert DispatchMode.CONCURRENT.value == "concurrent"
    assert isinstance(DispatchMode.SERIAL, str)
