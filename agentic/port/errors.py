# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Disjoint error families for the substrate port boundary.

``SubstrateError`` is the only family adapters may construct. It crosses the
port exclusively inside ``RequestFailed`` events — adapters never raise into
agentic code.

``AgenticError`` lives above the port. Adapters must not import or raise it:
doing so would push agentic semantics into every substrate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SubstrateError:
    """Base for substrate-level failures reported via ``RequestFailed``.

    :param detail: Optional human-readable context for logs and metrics.
    """

    detail: str = ""


@dataclass(frozen=True, slots=True)
class Unreachable(SubstrateError):
    """The named endpoint could not be reached on the wire."""


@dataclass(frozen=True, slots=True)
class Malformed(SubstrateError):
    """A request or response could not be decoded by the substrate."""


@dataclass(frozen=True, slots=True)
class TransportClosed(SubstrateError):
    """The underlying transport closed before the exchange completed."""


@dataclass(frozen=True, slots=True)
class AgenticError:
    """Base for failures raised above the port.

    Never crosses downward into an adapter. Listed here so the two families
    stay one documented vocabulary; adapters must not import this type.

    :param detail: Optional human-readable context for logs and metrics.
    """

    detail: str = ""


@dataclass(frozen=True, slots=True)
class NoTemplateMatched(AgenticError):
    """No signed task-graph template matched the inbound intent."""


@dataclass(frozen=True, slots=True)
class BoundExceeded(AgenticError):
    """A fan-out or depth bound would be exceeded by the chosen template."""


@dataclass(frozen=True, slots=True)
class DescriptorInvalid(AgenticError):
    """A capability descriptor failed verification or schema checks."""


@dataclass(frozen=True, slots=True)
class AggregationFailed(AgenticError):
    """Aggregation could not produce a valid final result for an intent."""
