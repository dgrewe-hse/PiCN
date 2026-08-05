# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Trust substrate: attestation, reputation, accountability log.

Must not import ``PiCN.*``. Signed artefact helpers live here so descriptors,
templates, and log entries share one envelope without pulling in the stack.
"""

from agentic.trust.jcs import FloatInSignedBodyError, jcs_dumps
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
    "ArtefactError",
    "ArtefactVerificationError",
    "FloatInSignedBodyError",
    "SignedArtefact",
    "generate_ed25519_private_key",
    "jcs_dumps",
    "sign_artefact",
    "verify_artefact",
]
