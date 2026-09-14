# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""A-012 directional longest-prefix demux: the h1/h10 prefix trap.

A Content satisfies an outstanding Interest iff the Interest name is a
component-prefix of the Content name -- never the reverse. The old
``_match_outstanding`` also accepted the reverse direction, so two outstanding
Interests whose names prefix one another could return the wrong correlation and
corrupt the Merkle trace root. These tests are RED on the pre-A-012 code.
"""

from __future__ import annotations

import pytest

from agentic.adapters.picn import PicnSubstratePort
from agentic.port.names import Name

BEDS = (b"cap", b"fwd", b"hospital", b"beds")


def _bed(suffix: bytes) -> Name:
    return Name(BEDS + (suffix,))


def _outstanding_port(*integers: bytes) -> PicnSubstratePort:
    """Port whose outstanding table holds ``h<int>`` Interests in given order."""
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    for integer in integers:
        port._outstanding[b"corr-" + integer] = _bed(integer)
    return port


def test_prefix_trap() -> None:
    """h10 Content must match the h10 Interest, not h1 (and vice versa)."""
    assert not _bed(b"h1").is_prefix_of(_bed(b"h10"))
    assert Name(BEDS + (b"h10",)).components[:4] == Name(BEDS).components

    # h1 registered first: dict insertion order would let the reverse clause
    # hand the h10 Content to the h1 Interest.
    h1_first = _outstanding_port(b"h1", b"h10")
    assert h1_first._match_outstanding(_bed(b"h10")) == b"corr-h10"
    assert h1_first._match_outstanding(_bed(b"h1")) == b"corr-h1"

    # h10 registered first: the reverse clause would hand the h1 Content to h10.
    h10_first = _outstanding_port(b"h10", b"h1")
    assert h10_first._match_outstanding(_bed(b"h1")) == b"corr-h1"
    assert h10_first._match_outstanding(_bed(b"h10")) == b"corr-h10"


def test_directional_lpm() -> None:
    """Content is accepted only when the Interest is a prefix of the Content."""
    port = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    port._outstanding[b"corr-parent"] = Name(BEDS)
    # Content deeper than the Interest: forward prefix → match.
    assert port._match_outstanding(_bed(b"h1")) == b"corr-parent"

    # Outstanding Interest deeper than the Content: Content name is a prefix of
    # the Interest name → no match under directional LPM.
    deeper = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    deeper._outstanding[b"corr-deep"] = _bed(b"h10")
    assert deeper._match_outstanding(Name(BEDS)) is None

    # Exact equality is subsumed by the forward prefix (inclusive).
    exact = picn = PicnSubstratePort("127.0.0.1", 9, log_level=255)
    exact._outstanding[b"corr-exact"] = _bed(b"h1")
    assert exact._match_outstanding(_bed(b"h1")) == b"corr-exact"


@pytest.mark.skip(reason="A-012 future token: wire carrier not delivered")
def test_token_exact_match() -> None:
    """Future: an echoed correlation token must win over any name match."""
    raise AssertionError("token carrier not implemented")


@pytest.mark.skip(reason="A-012 future token: wire carrier not delivered")
def test_lpm_fallback_no_token() -> None:
    """Future: without a token, demux must fall back to directional LPM."""
    raise AssertionError("token carrier not implemented")