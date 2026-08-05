# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Runtime selection guard for agentic nodes."""

from __future__ import annotations


class SyncRuntimeNotSupported(ValueError):
    """Raised when an agentic node is configured with ``runtime=\"sync\"``.

    The agentic layer is async-only: there is no sync wrapper, and the design
    (awaited aggregation, commit-before-forward ordering) cannot be expressed
    on the multiprocessing path.
    """


def require_async_runtime(runtime: str) -> None:
    """Refuse anything other than ``\"async\"``.

    :param runtime: Requested runtime string (``\"sync\"`` or ``\"async\"``).
    :raises SyncRuntimeNotSupported: If ``runtime`` is not ``\"async\"``.
    """
    if runtime != "async":
        raise SyncRuntimeNotSupported(
            f"AgenticLayer requires runtime='async' (got {runtime!r}). "
            "There is no sync wrapper for the agentic layer."
        )
