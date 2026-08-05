# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""World truth for the cardiac scenario (independent of agent self-reports)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class HospitalWorld:
    """Ground-truth hospital state used by the outcome log and KP-A.

    :param agent_id: Opaque agent identity string (hex of SPKI digest).
    :param true_beds: Beds actually free (simulator truth).
    :param advertised_beds: What the agent will claim (may be inflated).
    :param adversarial: True when advertised_beds intentionally lies.
    """

    agent_id: str
    true_beds: int
    advertised_beds: int
    adversarial: bool = False


@dataclass(frozen=True, slots=True)
class IntakeOutcome:
    """One real intake event written by the world simulator.

    :param hospital_id: Hospital that received (or redirected) the ambulance.
    :param accepted: Whether the patient was accepted.
    :param beds_at_arrival: Observed free beds at arrival (ground truth).
    :param at_ms: Injected clock reading.
    """

    hospital_id: str
    accepted: bool
    beds_at_arrival: int
    at_ms: int


@dataclass
class AmbulanceOutcomeLog:
    """Independently populated outcome log — never derived from hospital claims."""

    _events: list[IntakeOutcome] = field(default_factory=list)

    def record(self, outcome: IntakeOutcome) -> None:
        self._events.append(outcome)

    def for_hospital(self, hospital_id: str) -> list[IntakeOutcome]:
        return [e for e in self._events if e.hospital_id == hospital_id]

    @property
    def events(self) -> tuple[IntakeOutcome, ...]:
        return tuple(self._events)


@dataclass
class WorldState:
    """Fixed synthetic world for one scenario run.

    :param hospitals: Ordered hospital ground truth.
    :param traffic_eta_s: ETA seconds returned by the traffic agent.
    :param outcomes: Ambulance outcome log (world truth).
    """

    hospitals: list[HospitalWorld]
    traffic_eta_s: int = 720
    outcomes: AmbulanceOutcomeLog = field(default_factory=AmbulanceOutcomeLog)

    def hospital(self, agent_id: str) -> HospitalWorld:
        for hospital in self.hospitals:
            if hospital.agent_id == agent_id:
                return hospital
        raise KeyError(agent_id)

    def simulate_intake(self, hospital_id: str, *, at_ms: int) -> IntakeOutcome:
        """Record what actually happened when the ambulance arrived.

        Acceptance requires at least one true free bed. Adversarial inflated
        claims do not change this — KP-A later compares claim vs outcome.
        """
        hospital = self.hospital(hospital_id)
        accepted = hospital.true_beds >= 1
        outcome = IntakeOutcome(
            hospital_id=hospital_id,
            accepted=accepted,
            beds_at_arrival=hospital.true_beds,
            at_ms=at_ms,
        )
        self.outcomes.record(outcome)
        return outcome


__all__ = [
    "AmbulanceOutcomeLog",
    "HospitalWorld",
    "IntakeOutcome",
    "WorldState",
]
