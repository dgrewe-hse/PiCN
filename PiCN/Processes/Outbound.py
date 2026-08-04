"""Outbound actions returned by layer cores (ADR-003 extract-core addendum).

Cores must not touch queue objects. Sync wrappers apply these with ``put``;
async wrappers with ``await put``.
"""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Outbound:
    """One packet to emit toward a neighbouring layer."""

    direction: Literal["lower", "higher"]
    item: Any
