# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Trust substrate: attestation, reputation, accountability log.

Must not import ``PiCN.*``. Signed artefact helpers live here so descriptors,
templates, and log entries share one envelope without pulling in the stack.
"""

from agentic.trust.accountability_log import ENTRY_TYPES, AccountabilityLog, LogError
from agentic.trust.attestation import (
    DEFAULT_VALID_FOR_S,
    ClaimRecord,
    QuoteError,
    VerifiedQuote,
    issue_quote,
    node_id_from_key,
    verify_quote,
)
from agentic.trust.jcs import FloatInSignedBodyError, jcs_dumps
from agentic.trust.merkle import (
    NULL_RESPONSE,
    NULL_STEER,
    PENDING,
    MerkleTree,
    inclusion_proof,
    leaf_digest,
    mth,
    verify_inclusion,
)
from agentic.trust.reputation import (
    ALPHA_0,
    BETA_0,
    DECAY_INTERVAL_S,
    DECAY_LAMBDA,
    MIN_OBSERVATIONS,
    WEIGHT_ATTESTED,
    WEIGHT_UNVERIFIED,
    ReputationState,
    ReputationTable,
)
from agentic.trust.signed_artefact import (
    ALLOWED_KINDS,
    ArtefactError,
    ArtefactVerificationError,
    SignedArtefact,
    generate_ed25519_private_key,
    sign_artefact,
    verify_artefact,
)

__all__ = [
    "ALLOWED_KINDS",
    "ALPHA_0",
    "AccountabilityLog",
    "ArtefactError",
    "ArtefactVerificationError",
    "BETA_0",
    "ClaimRecord",
    "DECAY_INTERVAL_S",
    "DECAY_LAMBDA",
    "DEFAULT_VALID_FOR_S",
    "ENTRY_TYPES",
    "FloatInSignedBodyError",
    "LogError",
    "MIN_OBSERVATIONS",
    "MerkleTree",
    "NULL_RESPONSE",
    "NULL_STEER",
    "PENDING",
    "QuoteError",
    "ReputationState",
    "ReputationTable",
    "SignedArtefact",
    "VerifiedQuote",
    "WEIGHT_ATTESTED",
    "WEIGHT_UNVERIFIED",
    "generate_ed25519_private_key",
    "inclusion_proof",
    "issue_quote",
    "jcs_dumps",
    "leaf_digest",
    "mth",
    "node_id_from_key",
    "sign_artefact",
    "verify_artefact",
    "verify_inclusion",
    "verify_quote",
]
