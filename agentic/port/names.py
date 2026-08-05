# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Substrate-neutral naming types for the port.

Hierarchical names are ordered byte components. Adapters map these onto the
concrete name representation of their forwarder; nothing here is stack-specific.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class Name:
    """Hierarchical name as an ordered sequence of opaque components.

    :param components: Name components in order from the root. Empty is allowed
        only as a sentinel; producers should prefer at least one component.
    """

    components: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.components, tuple):
            object.__setattr__(self, "components", tuple(self.components))

    def is_prefix_of(self, other: Name) -> bool:
        """Return True if this name is a prefix of ``other`` (inclusive).

        :param other: Candidate longer-or-equal name.
        :return: Whether ``self.components`` is a prefix of ``other.components``.
        """
        n = len(self.components)
        if n > len(other.components):
            return False
        return other.components[:n] == self.components

    def __len__(self) -> int:
        return len(self.components)


@dataclass(frozen=True, slots=True)
class EndpointRef:
    """Opaque endpoint handle.

    Agentic code passes these through without interpreting them. Adapters
    define what the bytes mean for their substrate (face id, session key, …).

    :param value: Adapter-defined endpoint identity.
    """

    value: bytes


@dataclass(frozen=True, slots=True)
class Match:
    """Result of a longest-prefix match against a prefix table.

    :param prefix: The matched table key (longest prefix of the query).
    :param endpoints: Endpoints registered under that prefix, in table order.
    """

    prefix: Name
    endpoints: tuple[EndpointRef, ...]


# Read-only view used by ``longest_prefix_match``. Concrete adapters may store
# a mutable mapping; the port only requires Mapping semantics.
PrefixTable: TypeAlias = Mapping[Name, Sequence[EndpointRef]]
