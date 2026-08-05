# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Run metadata gates (seed, transport, reproducibility stamp)."""

from __future__ import annotations

import platform
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Literal

from agentic.benchmark.errors import HarnessError

Transport = Literal["bus", "udp"]


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """Required metadata for any written measurement result.

    :param seed: RNG / scenario seed (required).
    :param transport: ``bus`` or ``udp`` (required).
    :param commit_hash: Git commit at run start.
    :param python_version: Interpreter version string.
    :param k: Scenario scale parameter.
    :param reproducible: False when ``--allow-dirty`` was used on a dirty tree.
    :param allow_dirty: Whether dirty trees were explicitly permitted.
    :param parameters: Additional free-form run parameters.
    """

    seed: int
    transport: Transport
    commit_hash: str
    python_version: str
    k: int
    reproducible: bool
    allow_dirty: bool = False
    parameters: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["parameters"] is None:
            payload["parameters"] = {}
        return payload


def require_run_metadata(
    *,
    seed: int | None,
    transport: str | None,
    k: int,
    allow_dirty: bool,
    dirty: bool,
    commit_hash: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> RunMetadata:
    """Validate and construct metadata; refuse incomplete or dirty runs.

    :raises HarnessError: When seed/transport missing or dirty without allow.
    """
    if seed is None:
        raise HarnessError("refusing to write results without a seed")
    if transport is None:
        raise HarnessError("refusing to write results without transport metadata")
    if transport not in ("bus", "udp"):
        raise HarnessError(f"transport must be 'bus' or 'udp', got {transport!r}")
    if dirty and not allow_dirty:
        raise HarnessError(
            "refusing to run from a dirty working tree without --allow-dirty"
        )
    reproducible = not dirty
    return RunMetadata(
        seed=seed,
        transport=transport,  # type: ignore[arg-type]
        commit_hash=commit_hash or _git_head() or "unknown",
        python_version=platform.python_version(),
        k=k,
        reproducible=reproducible,
        allow_dirty=allow_dirty,
        parameters=parameters,
    )


def working_tree_dirty(*, cwd: str | None = None) -> bool:
    """Return True if ``git status --porcelain`` reports any change."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except OSError:
        # No git available — treat as dirty so results are stamped carefully.
        return True
    if completed.returncode != 0:
        return True
    return bool(completed.stdout.strip())


def _git_head(*, cwd: str | None = None) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


__all__ = [
    "RunMetadata",
    "Transport",
    "require_run_metadata",
    "working_tree_dirty",
]
