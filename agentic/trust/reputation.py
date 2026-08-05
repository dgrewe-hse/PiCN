# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Beta reputation model with lazy decay and atomic JSON snapshots.

De-prioritises below-threshold agents; never bans (invariant I8). Decisions
must go through :meth:`ReputationTable.score` — never read raw ``alpha`` /
``beta``. The clock is injected; this module never reads the wall clock.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# PROVISIONAL: cold-start prior Beta(1,1). Overturn if quarantine-FIB data shows
# honest newcomers stall under this prior (A-009).
ALPHA_0: float = 1.0
BETA_0: float = 1.0

# PROVISIONAL: decay toward the prior; derive λ and interval from M4 curves.
DECAY_LAMBDA: float = 0.99
DECAY_INTERVAL_S: float = 3600.0

# PROVISIONAL: minimum observations before leaving quarantine gating (I8).
MIN_OBSERVATIONS: int = 5

# Evidence weights supplied by the caller (A-009 rule 7).
WEIGHT_ATTESTED: float = 1.0
WEIGHT_UNVERIFIED: float = 0.3


@dataclass
class ReputationState:
    """Posterior state for one ``(agent_id, capability_domain)`` pair.

    :param alpha: Success pseudo-count (includes prior).
    :param beta: Failure pseudo-count (includes prior).
    :param last_update: Epoch seconds at last observe / materialised decay.
    :param observations: Monotonic observation count (never decayed).
    """

    alpha: float = ALPHA_0
    beta: float = BETA_0
    last_update: float = 0.0
    observations: int = 0


def _decayed_ab(state: ReputationState, now: float) -> tuple[float, float]:
    """Return ``(alpha, beta)`` after lazy decay to ``now`` (does not mutate)."""
    if state.observations == 0:
        return ALPHA_0, BETA_0
    dt = max(0.0, now - state.last_update)
    if dt == 0.0:
        return state.alpha, state.beta
    lam = DECAY_LAMBDA ** (dt / DECAY_INTERVAL_S) if DECAY_INTERVAL_S > 0 else 0.0
    a = ALPHA_0 + lam * (state.alpha - ALPHA_0)
    b = BETA_0 + lam * (state.beta - BETA_0)
    return a, b


def _mean_var(alpha: float, beta: float) -> tuple[float, float]:
    total = alpha + beta
    mean = alpha / total
    var = (alpha * beta) / (total * total * (total + 1.0))
    return mean, var


class ReputationTable:
    """In-memory Beta reputation table with optional JSON persistence.

    :param path: Optional snapshot path; loaded on construction if present.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._states: dict[tuple[str, str], ReputationState] = {}
        if self._path is not None and self._path.is_file():
            self.load(self._path)

    def _key(self, agent_id: bytes | str, capability_domain: str) -> tuple[str, str]:
        if isinstance(agent_id, bytes):
            agent = agent_id.hex()
        else:
            agent = agent_id
        return agent, capability_domain

    def _get_or_create(
        self, agent_id: bytes | str, capability_domain: str
    ) -> ReputationState:
        key = self._key(agent_id, capability_domain)
        state = self._states.get(key)
        if state is None:
            state = ReputationState()
            self._states[key] = state
        return state

    def score(
        self,
        agent_id: bytes | str,
        capability_domain: str,
        now: float,
    ) -> tuple[float, float, int]:
        """Decay lazily, then return ``(mean, variance, observations)``.

        :param agent_id: Agent identity (bytes or hex string).
        :param capability_domain: Capability domain key.
        :param now: Injected clock (epoch seconds).
        :return: Mean in [0, 1], variance, and monotonic observation count.
        """
        state = self._get_or_create(agent_id, capability_domain)
        a, b = _decayed_ab(state, now)
        mean, var = _mean_var(a, b)
        return mean, var, state.observations

    def observe(
        self,
        agent_id: bytes | str,
        capability_domain: str,
        *,
        success: bool,
        weight: float,
        now: float,
    ) -> tuple[float, float, int]:
        """Record one observation after decaying to ``now``.

        :param success: True for a verified positive outcome.
        :param weight: Evidence weight (caller-supplied; e.g. attested vs unverified).
        :param now: Injected clock.
        :return: Updated ``(mean, variance, observations)``.
        """
        if weight < 0.0:
            raise ValueError("weight must be non-negative")
        state = self._get_or_create(agent_id, capability_domain)
        a, b = _decayed_ab(state, now)
        if success:
            a += weight
        else:
            b += weight
        state.alpha = a
        state.beta = b
        state.last_update = now
        state.observations += 1
        return self.score(agent_id, capability_domain, now)

    def in_quarantine(
        self,
        agent_id: bytes | str,
        capability_domain: str,
        now: float,
    ) -> bool:
        """True while ``observations < MIN_OBSERVATIONS`` (I8 cold-start gate)."""
        _, _, observations = self.score(agent_id, capability_domain, now)
        return observations < MIN_OBSERVATIONS

    def as_scorer(self, capability_domain: str) -> "_MeanReputationScorer":
        """Adapt to the C-FIB :class:`~agentic.agentic_layer.cfib.ReputationScorer` seam."""
        return _MeanReputationScorer(self, capability_domain)

    def save(self, path: Path | str | None = None) -> None:
        """Atomically write a JSON snapshot (temp + fsync + rename)."""
        target = Path(path) if path is not None else self._path
        if target is None:
            raise ValueError("no snapshot path configured")
        payload = {
            "version": 1,
            "entries": [
                {
                    "agent_id": agent,
                    "capability_domain": domain,
                    "alpha": state.alpha,
                    "beta": state.beta,
                    "last_update": state.last_update,
                    "observations": state.observations,
                }
                for (agent, domain), state in sorted(self._states.items())
            ],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)

    def load(self, path: Path | str) -> None:
        """Replace in-memory state from a JSON snapshot."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = raw.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("invalid reputation snapshot")
        states: dict[tuple[str, str], ReputationState] = {}
        for item in entries:
            if not isinstance(item, Mapping):
                raise ValueError("invalid reputation entry")
            key = (str(item["agent_id"]), str(item["capability_domain"]))
            states[key] = ReputationState(
                alpha=float(item["alpha"]),
                beta=float(item["beta"]),
                last_update=float(item["last_update"]),
                observations=int(item["observations"]),
            )
        self._states = states
        self._path = Path(path)


@dataclass
class _MeanReputationScorer:
    """C-FIB adapter: exposes only the mean via ``score(identity, now=...)``."""

    table: ReputationTable
    capability_domain: str

    def score(self, identity: bytes, *, now: float) -> float:
        mean, _, _ = self.table.score(identity, self.capability_domain, now)
        return mean


__all__ = [
    "ALPHA_0",
    "BETA_0",
    "DECAY_INTERVAL_S",
    "DECAY_LAMBDA",
    "MIN_OBSERVATIONS",
    "WEIGHT_ATTESTED",
    "WEIGHT_UNVERIFIED",
    "ReputationState",
    "ReputationTable",
]
