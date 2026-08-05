# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Capability descriptor parsing and registration-time verification.

Descriptors are verified and parsed **once** at registration; the forwarding
path must use the cached object and never re-parse JSON per packet. Numeric
fields that participate in signatures are strings (or scaled integers), not
floats.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from agentic.agentic_layer.naming import (
    VERSION_MARKER,
    CapabilityNamingError,
    capability_name,
    issuer_digest_matches,
    parse_capability_name,
)
from agentic.port.errors import DescriptorInvalid
from agentic.port.names import Name
from agentic.trust.signed_artefact import (
    ArtefactVerificationError,
    SignedArtefact,
    verify_artefact,
)

AttestationPolicy = Literal["required", "optional", "none"]
_ATTESTATION_POLICIES = frozenset({"required", "optional", "none"})


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """Validated capability descriptor (registration cache entry).

    :param name: Capability name bound to the signing key.
    :param domain: Capability domain label.
    :param task: Task label within the domain.
    :param version: Semver string (also encoded in ``name``).
    :param constraints: Jurisdiction / latency / cost constraint object.
    :param attestation_policy: Whether attestation is required.
    :param reputation_threshold: Minimum score as a decimal string in ``[0,1]``.
    :param cost: Abstract cost as a decimal string or integer string.
    :param input_schema: JSON Schema for inputs.
    :param output_schema: JSON Schema for outputs.
    :param freshness_bound_s: Max age in seconds before re-validation.
    :param revocation_pointer: Where to check revocation status.
    :param public_key_der: Issuer public key DER from the envelope.
    :param artefact: Verified signed artefact (kept for re-checks).
    """

    name: Name
    domain: str
    task: str
    version: str
    constraints: Mapping[str, Any]
    attestation_policy: AttestationPolicy
    reputation_threshold: str
    cost: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    freshness_bound_s: int
    revocation_pointer: str
    public_key_der: bytes
    artefact: SignedArtefact


def _require_str(body: Mapping[str, Any], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value:
        raise DescriptorInvalid(detail=f"descriptor.{field} must be a non-empty string")
    return value


def _require_mapping(body: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = body.get(field)
    if not isinstance(value, dict):
        raise DescriptorInvalid(detail=f"descriptor.{field} must be an object")
    return value


def _require_int(body: Mapping[str, Any], field: str) -> int:
    value = body.get(field)
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise DescriptorInvalid(
            detail=f"descriptor.{field} must be a non-negative integer"
        )
    return value


def parse_descriptor_body(
    artefact: SignedArtefact, *, capability_path: tuple[bytes, ...]
) -> CapabilityDescriptor:
    """Build a :class:`CapabilityDescriptor` from a verified artefact.

    :param artefact: Already-verified signed artefact.
    :param capability_path: Path components under ``/cap/<issuer>/…`` (excluding
        version); must not contain ``v=``-prefixed components.
    :return: Parsed descriptor bound to ``artefact.public_key_der``.
    :raises DescriptorInvalid: On schema/field/name-binding failures.
    """
    if artefact.kind != "capability-descriptor":
        raise DescriptorInvalid(
            detail=f"expected kind capability-descriptor, got {artefact.kind!r}"
        )
    body = artefact.body
    for component in capability_path:
        if component.startswith(VERSION_MARKER):
            raise DescriptorInvalid(
                detail=f"capability path component must not start with v=: {component!r}"
            )
        if not component:
            raise DescriptorInvalid(detail="capability path components must be non-empty")

    domain = _require_str(body, "domain")
    task = _require_str(body, "task")
    version = _require_str(body, "version")
    constraints = _require_mapping(body, "constraints")
    policy = body.get("attestation_policy")
    assert policy in _ATTESTATION_POLICIES
    reputation_threshold = _require_str(body, "reputation_threshold")
    cost = _require_str(body, "cost")
    input_schema = _require_mapping(body, "input_schema")
    output_schema = _require_mapping(body, "output_schema")
    freshness_bound_s = _require_int(body, "freshness_bound_s")
    revocation_pointer = _require_str(body, "revocation_pointer")

    try:
        name = capability_name(artefact.public_key_der, capability_path, version)
    except CapabilityNamingError as exc:
        raise DescriptorInvalid(detail=str(exc)) from exc

    if not issuer_digest_matches(name, artefact.public_key_der):
        # Defensive — capability_name already binds the digest; keep the check
        # explicit so registration cannot skip it.
        raise DescriptorInvalid(detail="issuer digest does not match signing key")

    return CapabilityDescriptor(
        name=name,
        domain=domain,
        task=task,
        version=version,
        constraints=constraints,
        attestation_policy=policy,  # narrowed by assert above
        reputation_threshold=reputation_threshold,
        cost=cost,
        input_schema=input_schema,
        output_schema=output_schema,
        freshness_bound_s=freshness_bound_s,
        revocation_pointer=revocation_pointer,
        public_key_der=artefact.public_key_der,
        artefact=artefact,
    )


def register_descriptor(
    envelope: Mapping[str, Any],
    *,
    capability_path: tuple[bytes, ...],
    expected_name: Name | None = None,
) -> CapabilityDescriptor:
    """Verify, parse, and return a descriptor for caching in the C-FIB.

    :param envelope: Signed artefact envelope.
    :param capability_path: Capability path components (no version).
    :param expected_name: If provided, must equal the name derived from the
        signing key and path (hard reject on mismatch — FIB forgery defence).
    :return: Cached descriptor object.
    :raises DescriptorInvalid: On verification or field failures.
    """
    try:
        artefact = verify_artefact(envelope)
    except ArtefactVerificationError as exc:
        raise DescriptorInvalid(detail=f"artefact verification failed: {exc}") from exc

    descriptor = parse_descriptor_body(artefact, capability_path=capability_path)

    if expected_name is not None and expected_name != descriptor.name:
        raise DescriptorInvalid(
            detail="declared capability name does not match signing-key binding"
        )

    if expected_name is not None:
        try:
            parsed = parse_capability_name(expected_name)
        except CapabilityNamingError as exc:
            raise DescriptorInvalid(detail=str(exc)) from exc
        if parsed.issuer_digest != descriptor.name.components[1]:
            raise DescriptorInvalid(
                detail="name issuer digest does not match descriptor signing key"
            )

    return descriptor
