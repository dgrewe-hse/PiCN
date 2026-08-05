# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""KP-A cross-verifier: claimed capacity trends vs ambulance outcomes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from agentic.scenario.cardiac.world import AmbulanceOutcomeLog
from agentic.trust.accountability_log import AccountabilityLog
from agentic.trust.jcs import jcs_dumps
from agentic.trust.reputation import WEIGHT_ATTESTED, ReputationTable


@dataclass(frozen=True, slots=True)
class ClaimSnapshot:
    """One hospital capacity claim recovered for verification.

    :param hospital_id: Agent identity string.
    :param beds_free: Claimed free beds.
    :param quote_id: Attestation reference only (I6).
    :param payload_digest: Digest stored in the accountability log.
    """

    hospital_id: str
    beds_free: int
    quote_id: str
    payload_digest: bytes


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """Result of comparing one claim against world outcomes.

    :param hospital_id: Subject hospital.
    :param match: True when claim is consistent with outcomes.
    :param detail: Human-readable reason (for artefacts / tests).
    """

    hospital_id: str
    match: bool
    detail: str


def claim_payload_digest(claim_body: dict[str, Any]) -> bytes:
    """Digest used when anchoring a claim in the accountability log."""
    return hashlib.sha256(jcs_dumps(claim_body)).digest()


class KPAVerifier:
    """Compare accountability-log claims against ambulance-outcome-log truth.

    This is real comparison logic: only world outcomes are fixed by the
    simulator; the mismatch is detected by reading both logs.
    """

    def __init__(
        self,
        *,
        capability_domain: str = "hospital/beds",
    ) -> None:
        self._capability_domain = capability_domain

    def detect_mismatches(
        self,
        claims: list[ClaimSnapshot],
        outcomes: AmbulanceOutcomeLog,
    ) -> list[VerificationOutcome]:
        """Return one outcome per claim that has a corresponding intake event."""
        results: list[VerificationOutcome] = []
        for claim in claims:
            events = outcomes.for_hospital(claim.hospital_id)
            if not events:
                results.append(
                    VerificationOutcome(
                        hospital_id=claim.hospital_id,
                        match=True,
                        detail="no outcome yet",
                    )
                )
                continue
            latest = events[-1]
            # Inflated claim: advertised more beds than were present at arrival.
            inflated = claim.beds_free > latest.beds_at_arrival
            if inflated:
                results.append(
                    VerificationOutcome(
                        hospital_id=claim.hospital_id,
                        match=False,
                        detail=(
                            f"claimed beds_free={claim.beds_free} but "
                            f"beds_at_arrival={latest.beds_at_arrival}"
                        ),
                    )
                )
            else:
                results.append(
                    VerificationOutcome(
                        hospital_id=claim.hospital_id,
                        match=True,
                        detail="claim consistent with outcome",
                    )
                )
        return results

    def apply_reputation(
        self,
        table: ReputationTable,
        outcomes: list[VerificationOutcome],
        *,
        agent_keys: dict[str, bytes],
        now: float,
        log: AccountabilityLog | None = None,
        at_ms: int = 0,
    ) -> list[VerificationOutcome]:
        """Record success/failure observations; never ban (I8).

        :param agent_keys: Map hospital_id → agent identity bytes for the table.
        """
        for outcome in outcomes:
            if outcome.detail == "no outcome yet":
                continue
            agent = agent_keys[outcome.hospital_id]
            table.observe(
                agent,
                self._capability_domain,
                success=outcome.match,
                weight=WEIGHT_ATTESTED,
                now=now,
            )
            if log is not None:
                digest = hashlib.sha256(
                    json.dumps(
                        {
                            "hospital_id": outcome.hospital_id,
                            "match": outcome.match,
                            "detail": outcome.detail,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode()
                ).digest()
                log.append(
                    entry_type="verification_outcome",
                    subject=outcome.hospital_id,
                    payload_digest=digest,
                    at_ms=at_ms,
                )
                log.append(
                    entry_type="reputation_updated",
                    subject=outcome.hospital_id,
                    payload_digest=digest,
                    at_ms=at_ms,
                )
        return outcomes


__all__ = [
    "ClaimSnapshot",
    "KPAVerifier",
    "VerificationOutcome",
    "claim_payload_digest",
]
