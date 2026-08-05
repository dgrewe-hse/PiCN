# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Closed lifecycle-event union pushed by adapters into the inbound queue.

The union is closed on purpose: adapters must not invent substrate-specific
event types. ``correlation`` is opaque to the adapter — echo it, never parse it.
Timestamps (``at``) use the same clock domain as ``deadline`` on send.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from agentic.port.errors import SubstrateError
from agentic.port.names import Name


@dataclass(frozen=True, slots=True)
class RequestSent:
    """The substrate accepted a request for forwarding.

    :param correlation: Opaque correlation token supplied by the agentic layer.
    :param name: Name under which the request was sent.
    :param at: Timestamp when the substrate accepted the send.
    """

    correlation: bytes
    name: Name
    at: float


@dataclass(frozen=True, slots=True)
class ResponseArrived:
    """A response matching an outstanding correlation arrived.

    :param correlation: Opaque correlation token echoed from the request.
    :param name: Name associated with the response.
    :param payload: Response body bytes.
    :param at: Timestamp when the substrate received the response.
    """

    correlation: bytes
    name: Name
    payload: bytes
    at: float


@dataclass(frozen=True, slots=True)
class RequestTimedOut:
    """No response arrived before the request deadline.

    :param correlation: Opaque correlation token of the timed-out request.
    :param name: Name under which the request was sent.
    :param at: Timestamp when the substrate declared the timeout.
    """

    correlation: bytes
    name: Name
    at: float


@dataclass(frozen=True, slots=True)
class RequestFailed:
    """The substrate failed to carry the request; reason is substrate-level.

    :param correlation: Opaque correlation token of the failed request.
    :param name: Name under which the request was sent.
    :param reason: Substrate failure (never an agentic error).
    :param at: Timestamp when the substrate recorded the failure.
    """

    correlation: bytes
    name: Name
    reason: SubstrateError
    at: float


@dataclass(frozen=True, slots=True)
class InboundRequest:
    """A request arrived for which this node is the producer.

    :param correlation: Opaque correlation token assigned for the reply path.
    :param name: Requested name.
    :param payload: Request body bytes.
    :param at: Timestamp when the substrate delivered the request.
    """

    correlation: bytes
    name: Name
    payload: bytes
    at: float


# Closed union — do not extend without updating every adapter and dispatcher.
SubstrateEvent: TypeAlias = (
    RequestSent | ResponseArrived | RequestTimedOut | RequestFailed | InboundRequest
)
