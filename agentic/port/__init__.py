# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Substrate port: typed Protocol and lifecycle events.

Defines the narrow boundary between the agentic layer and any underlying
forwarder. No stack-specific types belong here — adapters translate at the edges.
"""

from agentic.port.errors import (
    AgenticError,
    AggregationFailed,
    BoundExceeded,
    CapacityExhausted,
    DescriptorInvalid,
    Malformed,
    NoTemplateMatched,
    SubstrateError,
    TransportClosed,
    Unreachable,
)
from agentic.port.events import (
    InboundRequest,
    RequestFailed,
    RequestSent,
    RequestTimedOut,
    ResponseArrived,
    SubstrateEvent,
)
from agentic.port.names import EndpointRef, Match, Name, PrefixTable
from agentic.port.protocol import SubstratePort

__all__ = [
    "AgenticError",
    "AggregationFailed",
    "BoundExceeded",
    "CapacityExhausted",
    "DescriptorInvalid",
    "EndpointRef",
    "InboundRequest",
    "Malformed",
    "Match",
    "Name",
    "NoTemplateMatched",
    "PrefixTable",
    "RequestFailed",
    "RequestSent",
    "RequestTimedOut",
    "ResponseArrived",
    "SubstrateError",
    "SubstrateEvent",
    "SubstratePort",
    "TransportClosed",
    "Unreachable",
]
