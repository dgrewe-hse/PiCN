# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""PiCN SimulationBus topologies: sync/async NFN vs async AgenticForwarder.

Same NFN combine interest on every strategy. PiCN imports are allowed here
(demo package). Keep ``agentic/benchmark`` free of ``PiCN.*`` (AC1).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal

from PiCN.Layers.LinkLayer.Interfaces import SimulationBus
from PiCN.Layers.PacketEncodingLayer.Encoder import NdnTlvEncoder
from PiCN.Mgmt import MgmtClient
from PiCN.Packets import Name
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.Fetch import Fetch
from PiCN.ProgramLibs.NFNForwarder import NFNForwarder
from PiCN.ProgramLibs.runtime import Runtime

NfnRuntimeName = Literal["sync", "async"]

_COMBINE_SRC = "PYTHON\nfunc\ndef func(a, b):\n    return str(a) + str(b)\n"


def unblock_sim_interfaces(*nodes: Any) -> None:
    """Wake SimulationInterface register() pumps blocked on queue_from_bus.get()."""
    for node in nodes:
        for iface in getattr(node, "interfaces", []) or []:
            q = getattr(iface, "queue_from_bus", None)
            if q is not None:
                try:
                    q.put(["teardown", b""])
                except Exception:
                    pass
            try:
                iface.close()
            except Exception:
                pass


def _mgmt_port(node: Any) -> int:
    """Return the TCP mgmt port for sync ``Mgmt`` or async ``AsyncMgmt``."""
    mgmt = node.mgmt
    port = getattr(mgmt, "port", None)
    if port is not None:
        return int(port)
    return int(mgmt.mgmt_sock.getsockname()[1])


def nfn_combine_interest(k: int) -> Name:
    """Identical NFN combine interest used by every bus strategy.

    Fan-out is capped at depth 4 (``k_eff = min(k, 4)``) because deeper
    nested ``combine`` expressions hang reliably on the sync NFN path under
    SimulationBus. Structural cardiac metrics still use the full ``k``.
    """
    k_eff = max(2, min(int(k), 4))
    expr = '"h0"'
    for i in range(1, k_eff):
        expr = f'_({expr},"h{i}")'
    name = Name("/func/combine")
    name += expr
    name += "NFN"
    return name


def _wire_bytes(interest: Name) -> int:
    return sum(len(c) for c in interest.components) + 16


@dataclass
class BusRunResult:
    """Timed SimulationBus exchange for one strategy.

    :param mode: ``nfn_sync``, ``nfn_async``, or ``agentic_async``.
    :param elapsed_ms: Wall-clock latency of the fetch.
    :param result: Decoded fetch payload (stringified).
    :param k: Fan-out parameter.
    :param seed: Run seed.
    :param simulated_interfaces: Faces on the bus.
    :param wire_bytes_estimate: Approximate interest name size.
    :param runtime: ``sync`` or ``async`` for the primary forwarder.
    """

    mode: str
    elapsed_ms: float
    result: str
    k: int
    seed: int
    simulated_interfaces: int
    wire_bytes_estimate: int
    runtime: str


def _configure_combine(mgmt_port: int, interest_prefix: Name = Name("/func")) -> None:
    """Install face, FIB, and combine function via blocking MgmtClient."""
    client = MgmtClient(mgmt_port)
    client.add_face("nfn1", None, 0)
    client.add_forwarding_rule(interest_prefix, [0])
    client.add_new_content(Name("/func/combine"), _COMBINE_SRC)


def run_nfn_bus_sync(*, k: int, seed: int, log_level: int = 255) -> BusRunResult:
    """Two sync ``NFNForwarder`` nodes + sync ``Fetch`` on SimulationBus."""
    # Unique face names avoid collisions when many sync buses are started
    # back-to-back in one process (mgmt/port reuse + lingering MP children).
    tag = f"{seed}-{k}-{time.time_ns() % 1_000_000}"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return _run_nfn_bus_sync_once(
                k=k, seed=seed, log_level=log_level, tag=f"{tag}-a{attempt}"
            )
        except Exception as exc:  # noqa: BLE001 — retry transient Empty/teardown races
            last_error = exc
            time.sleep(0.35 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _run_nfn_bus_sync_once(
    *, k: int, seed: int, log_level: int, tag: str
) -> BusRunResult:
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder)
    addr0 = f"nfn0-{tag}"
    addr1 = f"nfn1-{tag}"
    addr_f = f"fetch-{tag}"
    nfn0 = NFNForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface(addr0)],
        log_level=log_level,
        ageing_interval=1,
        runtime=Runtime.SYNC,
    )
    nfn1 = NFNForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface(addr1)],
        log_level=log_level,
        ageing_interval=1,
        runtime=Runtime.SYNC,
    )
    fetch = Fetch(
        addr0,
        None,
        log_level,
        encoder,
        interfaces=[bus.add_interface(addr_f)],
        runtime=Runtime.SYNC,
    )
    interest = nfn_combine_interest(k)
    wire_est = _wire_bytes(interest)
    nfn0.start_forwarder()
    nfn1.start_forwarder()
    bus.start_process()
    time.sleep(0.05)
    mgmt_client: MgmtClient | None = None
    try:
        mgmt_client = MgmtClient(_mgmt_port(nfn0))
        mgmt_client.add_face(addr1, None, 0)
        mgmt_client.add_forwarding_rule(Name("/func"), [0])
        mgmt_client.add_new_content(Name("/func/combine"), _COMBINE_SRC)
        started = time.perf_counter()
        result = fetch.fetch_data(interest, timeout=20)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if result is None:
            raise RuntimeError("sync NFN fetch returned None")
        return BusRunResult(
            mode="nfn_sync",
            elapsed_ms=elapsed_ms,
            result=str(result),
            k=k,
            seed=seed,
            simulated_interfaces=3,
            wire_bytes_estimate=wire_est,
            runtime="sync",
        )
    finally:
        if mgmt_client is not None:
            try:
                mgmt_client.shutdown()
            except Exception:
                pass
        try:
            fetch.stop_fetch()
        except Exception:
            pass
        unblock_sim_interfaces(nfn0, nfn1, fetch)
        try:
            nfn0.stop_forwarder()
        except Exception:
            pass
        try:
            nfn1.stop_forwarder()
        except Exception:
            pass
        try:
            bus.stop_process()
        except Exception:
            pass
        time.sleep(0.1)


async def run_nfn_bus_async(*, k: int, seed: int, log_level: int = 255) -> BusRunResult:
    """Two async ``NFNForwarder`` nodes + async ``Fetch`` on SimulationBus."""
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder)
    nfn0 = NFNForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface("nfn0")],
        log_level=log_level,
        ageing_interval=1,
        runtime=Runtime.ASYNC,
    )
    nfn1 = NFNForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface("nfn1")],
        log_level=log_level,
        ageing_interval=1,
        runtime=Runtime.ASYNC,
    )
    fetch = Fetch(
        "nfn0",
        None,
        log_level,
        encoder,
        interfaces=[bus.add_interface("fetch")],
        runtime=Runtime.ASYNC,
    )
    interest = nfn_combine_interest(k)
    wire_est = _wire_bytes(interest)
    await nfn0.start_forwarder_async()
    await nfn1.start_forwarder_async()
    await fetch.start_fetch_async()
    bus.start_process()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _configure_combine, _mgmt_port(nfn0))
        started = time.perf_counter()
        result = await fetch.fetch_data_async(interest, timeout=20)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return BusRunResult(
            mode="nfn_async",
            elapsed_ms=elapsed_ms,
            result=str(result),
            k=k,
            seed=seed,
            simulated_interfaces=3,
            wire_bytes_estimate=wire_est,
            runtime="async",
        )
    finally:
        unblock_sim_interfaces(nfn0, nfn1, fetch)
        await fetch.stop_fetch_async()
        await nfn0.stop_forwarder_async()
        await nfn1.stop_forwarder_async()
        bus.stop_process()


async def run_nfn_bus(
    *,
    k: int,
    seed: int,
    runtime: NfnRuntimeName | Runtime = "async",
    log_level: int = 255,
) -> BusRunResult:
    """Run plain NFN on SimulationBus under ``sync`` or ``async`` runtime.

    Sync topologies are executed in a ``spawn`` subprocess so multiprocessing
    ``fork`` is not taken from a multi-threaded asyncio parent (which is the
    usual cause of intermittent ``Empty`` timeouts under repeated campaigns).
    """
    if isinstance(runtime, Runtime):
        runtime_name: NfnRuntimeName = (
            "async" if runtime is Runtime.ASYNC else "sync"
        )
    else:
        runtime_name = runtime
    if runtime_name == "sync":
        return await asyncio.to_thread(
            _run_nfn_bus_sync_spawn, k=k, seed=seed, log_level=log_level
        )
    return await run_nfn_bus_async(k=k, seed=seed, log_level=log_level)


def _run_nfn_bus_sync_spawn(*, k: int, seed: int, log_level: int) -> BusRunResult:
    """Run sync NFN in a fresh interpreter (avoids asyncio×fork races)."""
    import json
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "demo._sync_nfn_worker",
            "--k",
            str(k),
            "--seed",
            str(seed),
            "--log-level",
            str(log_level),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not proc.stdout.strip():
        raise RuntimeError(
            f"sync NFN worker produced no stdout "
            f"(code={proc.returncode}, stderr={proc.stderr[-500:]})"
        )
    # Prefer the last JSON line (workers may emit logging to stderr only).
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(
            f"sync NFN worker produced no JSON "
            f"(code={proc.returncode}, stderr={proc.stderr[-500:]})"
        )
    payload = json.loads(lines[-1])
    if not payload.get("ok"):
        raise RuntimeError(f"sync NFN worker failed: {payload.get('error')}")
    return BusRunResult(
        mode=str(payload["mode"]),
        elapsed_ms=float(payload["elapsed_ms"]),
        result=str(payload["result"]),
        k=int(payload["k"]),
        seed=int(payload["seed"]),
        simulated_interfaces=int(payload["simulated_interfaces"]),
        wire_bytes_estimate=int(payload["wire_bytes_estimate"]),
        runtime=str(payload["runtime"]),
    )


async def run_agentic_bus(*, k: int, seed: int, log_level: int = 255) -> BusRunResult:
    """Async ``AgenticForwarder`` + async companion NFN + Fetch; same interest.

    ``AgenticForwarder`` is async-only (A-002). The companion NFN and Fetch use
    ``Runtime.ASYNC`` on the same SimulationBus and event loop.
    """
    encoder = NdnTlvEncoder()
    bus = SimulationBus(packetencoder=encoder)
    agentic = AgenticForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface("agentic0")],
        log_level=log_level,
        ageing_interval=1,
        runtime=Runtime.ASYNC,
    )
    nfn1 = NFNForwarder(
        port=0,
        encoder=encoder,
        interfaces=[bus.add_interface("nfn1")],
        log_level=log_level,
        ageing_interval=1,
        runtime=Runtime.ASYNC,
    )
    fetch = Fetch(
        "agentic0",
        None,
        log_level,
        encoder,
        interfaces=[bus.add_interface("fetch")],
        runtime=Runtime.ASYNC,
    )
    interest = nfn_combine_interest(k)
    wire_est = _wire_bytes(interest)
    await agentic.start_forwarder_async()
    await nfn1.start_forwarder_async()
    await fetch.start_fetch_async()
    bus.start_process()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _configure_combine, _mgmt_port(agentic))
        started = time.perf_counter()
        result = await fetch.fetch_data_async(interest, timeout=20)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return BusRunResult(
            mode="agentic_async",
            elapsed_ms=elapsed_ms,
            result=str(result),
            k=k,
            seed=seed,
            simulated_interfaces=3,
            wire_bytes_estimate=wire_est,
            runtime="async",
        )
    finally:
        unblock_sim_interfaces(agentic, nfn1, fetch)
        await fetch.stop_fetch_async()
        await agentic.stop_forwarder_async()
        await nfn1.stop_forwarder_async()
        bus.stop_process()


async def run_plain_nfn_bus(*, k: int, seed: int, log_level: int = 255) -> BusRunResult:
    """Backward-compatible alias: async plain NFN on SimulationBus."""
    return await run_nfn_bus(k=k, seed=seed, runtime="async", log_level=log_level)


__all__ = [
    "BusRunResult",
    "nfn_combine_interest",
    "run_agentic_bus",
    "run_nfn_bus",
    "run_nfn_bus_async",
    "run_nfn_bus_sync",
    "run_plain_nfn_bus",
    "unblock_sim_interfaces",
]
