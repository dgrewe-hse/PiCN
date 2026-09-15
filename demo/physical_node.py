# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Per-node entrypoint for the Experiment E physical testbed.

One process per Raspberry Pi node, selected by ``PICN_ROLE`` — systemd-friendly
(a single awaitable serving loop, graceful SIGTERM, per-node run markers
written to ``$PICN_RESULTS_DIR/markers/<run-id>-<host>.json``).

Env contract (design v4 §5.6):

    PICN_ROLE            edge | producer | llm | observer | intake
    PICN_EDGE_ID         edge1 .. edgeN
    PICN_LISTEN_PORT     UDP port this node serves on (0 = ephemeral)
    PICN_PEER_HOST       peer host (intake / upstream edge)
    PICN_PEER_PORT       peer UDP port
    PICN_OWNED_PREFIXES  comma-separated capability prefixes owned here
    PICN_BACKEND         deterministic | llm
    PICN_LLM_PLACEMENT   on-pi | off-pi (backend=llm only)
    PICN_MODEL_CONFIG    model preference TOML (backend=llm only)
    PICN_RESULTS_DIR     results root (markers/, service_records/, JSONL)
    PICN_RUN_ID          campaign run id
    PICN_SEED / PICN_K / PICN_LEAF_LATENCY_S / PICN_EDGES   intake campaign
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from PiCN.Layers.LinkLayer.Interfaces import UDP4Interface

from demo.physical_topology import _make_backend, _descriptor_for

ENV_KEYS: tuple[str, ...] = (
    "PICN_ROLE",
    "PICN_EDGE_ID",
    "PICN_LISTEN_PORT",
    "PICN_PEER_HOST",
    "PICN_PEER_PORT",
    "PICN_OWNED_PREFIXES",
    "PICN_BACKEND",
    "PICN_LLM_PLACEMENT",
    "PICN_MODEL_CONFIG",
    "PICN_RESULTS_DIR",
    "PICN_RUN_ID",
    "PICN_SEED",
    "PICN_K",
    "PICN_LEAF_LATENCY_S",
    "PICN_EDGES",
)

ROLES = ("edge", "producer", "llm", "observer", "intake")


@dataclasses.dataclass(frozen=True)
class NodeConfig:
    """Parsed node environment (see :data:`ENV_KEYS`)."""

    role: str
    edge_id: str
    listen_port: int
    peer_host: str
    peer_port: int | None
    owned_prefixes: list[str]
    backend: str
    llm_placement: str | None
    model_config: str | None
    results_dir: str
    run_id: str
    seed: int
    k: int
    leaf_latency_s: float | None
    edges: int


def _get(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "")
    if not value:
        raise SystemExit(f"missing required environment variable {key}")
    return value


def read_env_config(env: Mapping[str, str] | None = None) -> NodeConfig:
    """Parse the node env contract; refuse incomplete environments.

    :raises SystemExit: When ``PICN_ROLE`` (or the role's required vars) miss.
    """
    env = env if env is not None else os.environ
    role = _get(env, "PICN_ROLE")
    if role not in ROLES:
        raise SystemExit(f"PICN_ROLE must be one of {ROLES}, got {role!r}")

    leaf_latency_raw = env.get("PICN_LEAF_LATENCY_S", "")
    peer_port_raw = env.get("PICN_PEER_PORT", "")
    return NodeConfig(
        role=role,
        edge_id=env.get("PICN_EDGE_ID", ""),
        listen_port=int(env.get("PICN_LISTEN_PORT", "0") or 0),
        peer_host=env.get("PICN_PEER_HOST", ""),
        peer_port=int(peer_port_raw) if peer_port_raw else None,
        owned_prefixes=[
            prefix.strip()
            for prefix in env.get("PICN_OWNED_PREFIXES", "").split(",")
            if prefix.strip()
        ],
        backend=env.get("PICN_BACKEND", "") or "deterministic",
        llm_placement=env.get("PICN_LLM_PLACEMENT", "") or None,
        model_config=env.get("PICN_MODEL_CONFIG", "") or None,
        results_dir=_get(env, "PICN_RESULTS_DIR"),
        run_id=_get(env, "PICN_RUN_ID"),
        seed=int(env.get("PICN_SEED", "0") or 0),
        k=int(env.get("PICN_K", "0") or 0),
        leaf_latency_s=float(leaf_latency_raw) if leaf_latency_raw else None,
        edges=int(env.get("PICN_EDGES", "1") or 1),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def marker_path(config: NodeConfig, host: str) -> Path:
    """Marker file for ``(run_id, host)``: ``markers/<run-id>-<host>.json``."""
    return Path(config.results_dir) / "markers" / f"{config.run_id}-{host}.json"


def write_start_marker(config: NodeConfig, *, host: str) -> dict[str, Any]:
    """Write the per-node run marker with the deployed-commit attestation."""
    disk = _git_head() or "unknown"
    marker = {
        "host": host,
        "role": config.role,
        "run_id": config.run_id,
        # deployed_commit: the PICN_COMMIT the deployment truth says this node
        # runs; falls back to the on-disk commit when absent.
        "deployed_commit": os.environ.get("PICN_COMMIT", disk),
        "disk_commit": disk,
        "owned_prefixes": config.owned_prefixes,
        "started_at": _now_iso(),
        "ended_at": None,
    }
    path = marker_path(config, host)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return marker


def write_end_marker(config: NodeConfig, handle: dict[str, Any], *, host: str) -> None:
    """Refresh the run marker with the node's ended_at timestamp."""
    marker = dict(handle)
    marker["ended_at"] = _now_iso()
    path = marker_path(config, host)
    path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_head() -> str | None:
    from agentic.benchmark.metadata import _git_head as git_head

    return git_head()


def _build_serving_config(config: NodeConfig):
    """Build the serving payload for a producer-bearing node."""
    serving: list[tuple[str, Any, Any]] = []
    for prefix in config.owned_prefixes:
        parts = tuple(part.encode("utf-8") for part in prefix.strip("/").split("/"))
        leaf_index = int(parts[-1].decode().lstrip("h"))
        backend, input_schema, output_schema = _make_backend(
            backend=config.backend,
            leaf_index=leaf_index,
            leaf_latency_s=config.leaf_latency_s,
            model_config=config.model_config,
            llm_placement=config.llm_placement,
        )
        serving.append((prefix, backend, (input_schema, output_schema)))
    return serving


async def serve_edge(
    config: NodeConfig,
    handle: dict[str, Any],
    *,
    host: str,
    soak_s: float | None = None,
) -> bool:
    """Serve this node's capability producers until SIGTERM.

    Registers every owned prefix with its backend on an ``AgenticForwarder``
    bound to ``PICN_LISTEN_PORT`` and serves until cancelled (systemd stop).
    Markers carry the deployed/disk commit attestation the gate consumes.
    ``soak_s`` bounds the serving window (tests); ``None`` serves until a
    signal arrives.

    :return: ``True`` when the node served and stopped cleanly.
    """
    # Local import: the serving stack shares the topology's node wiring.
    from PiCN.Layers.PacketEncodingLayer.Encoder import NdnTlvEncoder
    from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
    from PiCN.ProgramLibs.runtime import Runtime

    from agentic.port.names import Name

    encoder = NdnTlvEncoder()
    forwarder = AgenticForwarder(
        port=config.listen_port,
        encoder=encoder,
        interfaces=[UDP4Interface(config.listen_port)],
        log_level=255,
        ageing_interval=1,
        runtime=Runtime.ASYNC,
    )
    serving = _build_serving_config(config)
    for prefix, backend, (input_schema, output_schema) in serving:
        # The registry is keyed by exact descriptor name: the deployed node
        # must register under the routed wire name (/cap/fwd/hospital/beds/hN)
        # — the issuer-bound capability name parse_descriptor_body produces
        # would never match the intake's Interest (same binding as
        # build_physical_topology's in-process edges).
        wire_name = Name(
            tuple(part.encode("utf-8") for part in prefix.strip("/").split("/"))
        )
        desc = _descriptor_for(wire_name, input_schema, output_schema)
        forwarder.register_capability(desc, backend, backend_label=config.backend)

    await forwarder.start_forwarder_async()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopped.set)
        except NotImplementedError:  # pragma: no cover - non-Unix
            pass
    waiter = asyncio.create_task(stopped.wait())
    try:
        await asyncio.wait([waiter], timeout=soak_s)
    finally:
        if not waiter.done():
            waiter.cancel()
        # systemd sends SIGTERM then waits; a wedged layer stop must not
        # hang the unit shutdown (same bounded stop as the topology).
        try:
            await asyncio.wait_for(
                asyncio.shield(forwarder.stop_forwarder_async()), timeout=5.0
            )
        except Exception:
            pass
        write_end_marker(config, handle, host=host)
    return True


def _intake_argv(config: NodeConfig) -> list[str]:
    argv = [
        "--edges", str(config.edges),
        "--seeds", str(config.seed or 1),
        "--k", str(config.k),
        "--runs-per-cell", os.environ.get("PICN_RUNS_PER_CELL", "30"),
        "--transport", "udp",
        "--run-id", config.run_id,
        "--out", str(
            Path(config.results_dir) / f"{config.run_id}.jsonl"
        ),
        "--observer", "on",
    ]
    if config.backend == "llm":
        argv.extend(["--backend", "llm", "--llm-placement", config.llm_placement or "on-pi"])
        if config.model_config:
            argv.extend(["--model-config", config.model_config])
    else:
        argv.extend(["--backend", "deterministic"])
        if config.leaf_latency_s is not None:
            argv.extend(["--leaf-latency-s", str(config.leaf_latency_s)])
    return argv


def main(argv: list[str] | None = None) -> int:
    """Node entrypoint: role dispatch from ``PICN_ROLE``."""
    _ = argv
    config = read_env_config()
    host = os.environ.get("PICN_NODE_HOST") or os.uname().nodename
    if config.role == "intake":
        from demo import run_physical

        return run_physical.main(_intake_argv(config))
    if config.role == "observer":
        handle = write_start_marker(config, host=host)
        write_end_marker(config, handle, host=host)
        return 0
    # edge / producer / llm: serve owned capability prefixes until SIGTERM.
    handle = write_start_marker(config, host=host)
    return 0 if asyncio.run(serve_edge(config, handle, host=host)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
