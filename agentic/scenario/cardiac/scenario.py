# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""CardiacScenario — end-to-end happy path and adversary sub-case."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.agentic_layer.aggregation import (
    default_steer_heads,
    maybe_complete,
    verify_claimed_root,
)
from agentic.agentic_layer.context_pit import ContextPIT
from agentic.agentic_layer.descriptor import CapabilityDescriptor, parse_descriptor_body
from agentic.binding import CapabilityRegistry, DeterministicBackend
from agentic.binding.schema import validate_against_schema
from agentic.scenario import register_scenario
from agentic.scenario.cardiac.kpa import (
    ClaimSnapshot,
    KPAVerifier,
    claim_payload_digest,
)
from agentic.scenario.cardiac.world import HospitalWorld, WorldState
from agentic.trust import (
    AccountabilityLog,
    ReputationTable,
    generate_ed25519_private_key,
    issue_quote,
    node_id_from_key,
    sign_artefact,
    verify_artefact,
    verify_quote,
)
from agentic.trust.merkle import NULL_RESPONSE
from agentic.trust.reputation import WEIGHT_UNVERIFIED


CAPABILITY_DOMAIN = "hospital/beds"

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"patient_id": {"type": "string"}},
    "required": ["patient_id"],
    "additionalProperties": False,
}
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"beds_free": {"type": "integer"}},
    "required": ["beds_free"],
    "additionalProperties": False,
}


def _digest(label: str) -> bytes:
    return hashlib.sha256(label.encode("utf-8")).digest()


def _public_der(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _make_descriptor(key: Ed25519PrivateKey) -> CapabilityDescriptor:
    body = {
        "kind": "capability-descriptor",
        "domain": "hospital",
        "task": "beds",
        "version": "1.0.0",
        "constraints": {"jurisdiction": "DE"},
        "attestation_policy": "optional",
        "reputation_threshold": "0.5",
        "cost": "1",
        "input_schema": _INPUT_SCHEMA,
        "output_schema": _OUTPUT_SCHEMA,
        "freshness_bound_s": 60,
        "revocation_pointer": "none",
    }
    return parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)),
        capability_path=(b"hospital", b"beds"),
    )


@dataclass
class HospitalActor:
    """One hospital producer with attestation material."""

    key: Ed25519PrivateKey
    public_der: bytes
    agent_id: str
    node_id: str
    true_beds: int
    advertised_beds: int
    adversarial: bool
    binary_hash: bytes = field(
        default_factory=lambda: hashlib.sha256(b"hospital-bin").digest()
    )

    @classmethod
    def create(
        cls,
        *,
        true_beds: int,
        advertised_beds: int,
        adversarial: bool = False,
    ) -> HospitalActor:
        key = generate_ed25519_private_key()
        public_der = _public_der(key)
        return cls(
            key=key,
            public_der=public_der,
            agent_id=public_der.hex(),
            node_id=node_id_from_key(public_der),
            true_beds=true_beds,
            advertised_beds=advertised_beds,
            adversarial=adversarial,
        )


@register_scenario
class CardiacScenario:
    """Pluggable cardiac-response scenario (spec/08).

    :cvar scenario_name: Registry key used by :func:`available_scenarios`.
    """

    scenario_name = "cardiac-response"

    def __init__(self, *, k: int = 3, seed: int = 0) -> None:
        if k < 2:
            raise ValueError("k must be >= 2 so an adversary can be one of many")
        self._k = k
        self._seed = seed
        self._clock_ms = 1_000 + seed
        self._hospitals: list[HospitalActor] = []
        self._backends: dict[str, DeterministicBackend] = {}
        self._world: WorldState | None = None
        self._pit = ContextPIT()
        self._registry = CapabilityRegistry()
        self._reputation = ReputationTable()
        self._log_key = generate_ed25519_private_key()
        self._log = AccountabilityLog(node_private_key=self._log_key)
        self._ambulance_key = generate_ed25519_private_key()
        self._desc: CapabilityDescriptor | None = None
        self._claims: list[ClaimSnapshot] = []
        self._mismatched_ids: set[str] = set()
        # Injected by the harness; never inspected for transport kind.
        self._port: Any = None

    @property
    def name(self) -> str:
        return self.scenario_name

    async def setup(self, **kwargs: Any) -> None:
        """Create actors and world state.

        :param port: Optional substrate port (transport-agnostic injection).
        :param k: Override hospital count.
        """
        self._port = kwargs.get("port")
        k = int(kwargs.get("k", self._k))
        if k < 2:
            raise ValueError("k must be >= 2")
        self._k = k
        self._hospitals = []
        for i in range(k):
            beds = 2 + i
            self._hospitals.append(
                HospitalActor.create(
                    true_beds=beds,
                    advertised_beds=beds,
                    adversarial=False,
                )
            )
        self._world = WorldState(
            hospitals=[
                HospitalWorld(
                    agent_id=h.agent_id,
                    true_beds=h.true_beds,
                    advertised_beds=h.advertised_beds,
                    adversarial=h.adversarial,
                )
                for h in self._hospitals
            ]
        )
        self._claims.clear()
        self._mismatched_ids.clear()
        self._reputation = ReputationTable()
        self._log = AccountabilityLog(node_private_key=self._log_key)
        self._pit = ContextPIT()
        self._registry = CapabilityRegistry()
        self._rebuild_backends()

    def _rebuild_backends(self) -> None:
        self._desc = _make_descriptor(self._ambulance_key)
        self._backends.clear()
        self._registry = CapabilityRegistry()
        for hospital in self._hospitals:
            beds = hospital.advertised_beds

            async def _handler(
                payload: dict[str, Any], *, _beds: int = beds
            ) -> dict[str, Any]:
                _ = payload
                return {"beds_free": _beds}

            backend = DeterministicBackend(
                _handler, input_schema=_INPUT_SCHEMA, output_schema=_OUTPUT_SCHEMA
            )
            self._backends[hospital.agent_id] = backend
        # One registration proves the shared CapabilityBackend path (AC).
        first = next(iter(self._backends.values()))
        self._registry.register(self._desc, first, backend_label="hospital-template")

    async def teardown(self) -> None:
        self._hospitals.clear()
        self._backends.clear()
        self._world = None
        self._claims.clear()
        self._port = None

    def _now_ms(self) -> int:
        self._clock_ms += 1
        return self._clock_ms

    def _now_s(self) -> float:
        return self._clock_ms / 1000.0

    async def _issue_hospital_response(
        self, hospital: HospitalActor, *, patient_id: str
    ) -> dict[str, Any]:
        """Produce a capacity response with a verified quote referenced by id."""
        assert self._desc is not None
        quote_env = issue_quote(
            node_private_key=hospital.key,
            binary_hash=hospital.binary_hash,
            issued_at_ms=self._now_ms(),
            valid_for_s=300,
            nonce=hashlib.sha256(hospital.agent_id.encode()).digest(),
        )
        verified = verify_quote(
            quote_env, expected_node_id=hospital.node_id, now_ms=self._clock_ms
        )
        request = {"patient_id": patient_id}
        validate_against_schema(request, self._desc.input_schema)
        backend = self._backends[hospital.agent_id]
        payload = await backend.invoke(request, deadline=1.0)
        validate_against_schema(payload, self._desc.output_schema)
        # Adversary may advertise above the deterministic stub if rebuilt mid-run.
        payload = {"beds_free": hospital.advertised_beds}
        claim_body = {
            "beds_free": hospital.advertised_beds,
            "patient_id": patient_id,
            "quote_id": verified.quote_id,
            "hospital_id": hospital.agent_id,
        }
        digest = claim_payload_digest(claim_body)
        self._log.append(
            entry_type="claim_recorded",
            subject=hospital.agent_id,
            payload_digest=digest,
            at_ms=self._now_ms(),
        )
        self._log.append(
            entry_type="quote_issued",
            subject=verified.quote_id,
            payload_digest=hashlib.sha256(verified.quote_id.encode()).digest(),
            at_ms=self._now_ms(),
        )
        self._claims.append(
            ClaimSnapshot(
                hospital_id=hospital.agent_id,
                beds_free=hospital.advertised_beds,
                quote_id=verified.quote_id,
                payload_digest=digest,
            )
        )
        # Unverified claim observation until KP-A confirms (I6). Skip agents
        # already mismatched so a later request cannot wash out the failure.
        if hospital.agent_id not in self._mismatched_ids:
            self._reputation.observe(
                hospital.public_der,
                CAPABILITY_DOMAIN,
                success=True,
                weight=WEIGHT_UNVERIFIED,
                now=self._now_s(),
            )
        return {
            "payload": payload,
            "quote_id": verified.quote_id,
            "claim_body": claim_body,
            "response_digest": hashlib.sha256(
                json.dumps(claim_body, separators=(",", ":"), sort_keys=True).encode()
            ).digest(),
        }

    def _rank_hospitals(self) -> list[dict[str, Any]]:
        """Edge ranking: reputation first, then beds; all remain eligible (I8).

        Reputation is primary so a KP-A failure de-prioritises an agent even
        when its (possibly inflated) bed claim is numerically large.
        """
        ranked: list[dict[str, Any]] = []
        now = self._now_s()
        for hospital in self._hospitals:
            mean, _, obs = self._reputation.score(
                hospital.public_der, CAPABILITY_DOMAIN, now
            )
            ranked.append(
                {
                    "hospital_id": hospital.agent_id,
                    "beds_free": hospital.advertised_beds,
                    "reputation_mean": mean,
                    "observations": obs,
                    "rank_score": mean,
                    "adversarial": hospital.adversarial,
                }
            )
        ranked.sort(
            key=lambda row: (row["reputation_mean"], row["beds_free"]),
            reverse=True,
        )
        return ranked

    async def _run_exchange(
        self, *, patient_id: str, parent_label: str
    ) -> dict[str, Any]:
        """Commit Context PIT, collect sub-responses, terminate, verify root."""
        assert self._world is not None
        issuer_der = _public_der(self._ambulance_key)
        parent = _digest(parent_label)

        leaf_specs: list[tuple[bytes, bytes, int | None, tuple[str, ...], bytes]] = [
            (_digest(f"{parent_label}:enrichment"), b"ecg", 50, ("enrichment",), b""),
        ]
        for hospital in self._hospitals:
            leaf_specs.append(
                (
                    _digest(f"{parent_label}:hospital:{hospital.agent_id}"),
                    patient_id.encode(),
                    200,
                    ("hospital", "beds"),
                    hospital.public_der,
                )
            )
        leaf_specs.append(
            (_digest(f"{parent_label}:traffic"), b"eta", 100, ("traffic",), b"")
        )
        leaf_specs.append(
            (_digest(f"{parent_label}:ranking"), b"rank", 100, ("ranking",), b"")
        )

        # I2: commit expected set before any forward.
        entry = self._pit.commit(
            parent_intent_digest=parent,
            issuer_public_key_der=issuer_der,
            aggregation_policy="ALL",
            leaf_specs=leaf_specs,
            now_ms=self._now_ms(),
        )

        hospital_leaf_offset = 1
        responses: list[dict[str, Any]] = []

        self._pit.mark_forwarded(parent, 0)
        self._pit.record_response(parent, 0, hashlib.sha256(b"enrichment-ok").digest())

        for index, hospital in enumerate(self._hospitals):
            leaf_index = hospital_leaf_offset + index
            self._pit.mark_forwarded(parent, leaf_index)
            response = await self._issue_hospital_response(
                hospital, patient_id=patient_id
            )
            responses.append(response)
            self._pit.record_response(parent, leaf_index, response["response_digest"])

        traffic_index = hospital_leaf_offset + len(self._hospitals)
        self._pit.mark_forwarded(parent, traffic_index)
        traffic_body = {"eta_s": self._world.traffic_eta_s}
        traffic_digest = hashlib.sha256(
            json.dumps(traffic_body, separators=(",", ":"), sort_keys=True).encode()
        ).digest()
        self._pit.record_response(parent, traffic_index, traffic_digest)

        ranking_index = traffic_index + 1
        self._pit.mark_forwarded(parent, ranking_index)
        ranking = self._rank_hospitals()
        ranking_digest = hashlib.sha256(
            json.dumps(ranking, separators=(",", ":"), sort_keys=True).encode()
        ).digest()
        self._pit.record_response(parent, ranking_index, ranking_digest)

        completed = maybe_complete(self._pit, parent, self._now_ms())
        assert completed is not None and completed.terminated
        entry = completed

        steer_heads = default_steer_heads(len(entry.leaves))
        response_digests = tuple(leaf.response_digest for leaf in entry.leaves)
        root_ok = verify_claimed_root(
            entry.expected_subintent_set,
            steer_heads,
            response_digests,
            entry.trace_root,
        )
        contributions = [
            {
                "capability": list(leaf.capability),
                "response_digest": leaf.response_digest.hex(),
                "null": leaf.response_digest == NULL_RESPONSE,
            }
            for leaf in entry.leaves
        ]

        self._log.append(
            entry_type="trace_root_anchored",
            subject=parent.hex(),
            payload_digest=entry.trace_root,
            at_ms=self._now_ms(),
        )

        return {
            "parent_intent_digest": parent.hex(),
            "trace_root": entry.trace_root.hex(),
            "trace_root_verified": root_ok,
            "contributions": contributions,
            "ranking": ranking,
            "hospital_responses": [
                {"quote_id": r["quote_id"], "beds_free": r["payload"]["beds_free"]}
                for r in responses
            ],
            "accountability_log_length": self._log.length,
        }

    async def run_happy_path(self, **kwargs: Any) -> dict[str, Any]:
        """Happy path: honest hospitals, verifying trace root, full contributions."""
        if self._world is None:
            await self.setup(**kwargs)
        assert self._world is not None
        patient_id = str(kwargs.get("patient_id", "p-happy"))
        artefacts = await self._run_exchange(
            patient_id=patient_id, parent_label="happy"
        )
        top = artefacts["ranking"][0]["hospital_id"]
        outcome = self._world.simulate_intake(top, at_ms=self._now_ms())
        artefacts["intake_outcome"] = {
            "hospital_id": outcome.hospital_id,
            "accepted": outcome.accepted,
            "beds_at_arrival": outcome.beds_at_arrival,
        }
        artefacts["path"] = "happy"
        return artefacts

    async def run_adversary(self, **kwargs: Any) -> dict[str, Any]:
        """Adversary sub-case: valid quote + false claim → KP-A → de-prioritise."""
        if self._world is None:
            await self.setup(**kwargs)
        assert self._world is not None

        adversary = self._hospitals[-1]
        adversary.adversarial = True
        adversary.true_beds = 0
        adversary.advertised_beds = 12
        self._world.hospitals[-1] = HospitalWorld(
            agent_id=adversary.agent_id,
            true_beds=0,
            advertised_beds=12,
            adversarial=True,
        )
        self._rebuild_backends()

        patient_id = str(kwargs.get("patient_id", "p-adv"))
        first = await self._run_exchange(patient_id=patient_id, parent_label="adv-1")

        # World truth independent of the (validly attested) inflated claim.
        intake = self._world.simulate_intake(adversary.agent_id, at_ms=self._now_ms())
        assert intake.accepted is False
        assert intake.beds_at_arrival == 0

        verifier = KPAVerifier(capability_domain=CAPABILITY_DOMAIN)
        verifications = verifier.detect_mismatches(self._claims, self._world.outcomes)
        mismatch = [v for v in verifications if not v.match]
        assert any(v.hospital_id == adversary.agent_id for v in mismatch)
        self._mismatched_ids.update(v.hospital_id for v in mismatch)

        agent_keys = {h.agent_id: h.public_der for h in self._hospitals}
        for _ in range(5):
            verifier.apply_reputation(
                self._reputation,
                [v for v in verifications if v.hospital_id == adversary.agent_id],
                agent_keys=agent_keys,
                now=self._now_s(),
                log=self._log,
                at_ms=self._now_ms(),
            )

        mean_after, _, obs_after = self._reputation.score(
            adversary.public_der, CAPABILITY_DOMAIN, self._now_s()
        )

        second = await self._run_exchange(
            patient_id=f"{patient_id}-later", parent_label="adv-2"
        )
        ranking = second["ranking"]
        ids = [row["hospital_id"] for row in ranking]
        assert adversary.agent_id in ids  # I8: de-prioritised, not excluded
        adv_rank = ids.index(adversary.agent_id)
        assert adv_rank > 0

        return {
            "path": "adversary",
            "first_exchange": first,
            "second_exchange": second,
            "adversary_id": adversary.agent_id,
            "inflated_claim_beds": adversary.advertised_beds,
            "true_beds": adversary.true_beds,
            "quote_valid_with_false_claim": True,
            "intake_redirected": not intake.accepted,
            "kpa_mismatches": [
                {"hospital_id": v.hospital_id, "detail": v.detail} for v in mismatch
            ],
            "reputation_after_mismatch": {
                "mean": mean_after,
                "observations": obs_after,
            },
            "adversary_rank_index": adv_rank,
            "still_eligible": True,
            "accountability_log_length": self._log.length,
            "trace_root_verified_first": first["trace_root_verified"],
            "trace_root_verified_second": second["trace_root_verified"],
        }


__all__ = ["CAPABILITY_DOMAIN", "CardiacScenario", "HospitalActor"]
