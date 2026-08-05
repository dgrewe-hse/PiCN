# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Name conversion between agentic port names and PiCN ``Packets.Name``."""

from __future__ import annotations

from collections.abc import Sequence

from PiCN.Packets import Name as PicnName

from agentic.port.names import Name as PortName


def to_picn_name(name: PortName) -> PicnName:
    """Convert a port ``Name`` to a PiCN ``Packets.Name``."""
    return PicnName(list(name.components))


def from_picn_name(name: PicnName) -> PortName:
    """Convert a PiCN ``Packets.Name`` to a port ``Name``."""
    comps = getattr(name, "components", None)
    if comps is None:
        comps = list(name)
    return PortName(tuple(bytes(c) if not isinstance(c, bytes) else c for c in comps))


def components_to_port_name(parts: Sequence[bytes]) -> PortName:
    return PortName(tuple(parts))


__all__ = ["components_to_port_name", "from_picn_name", "to_picn_name"]
