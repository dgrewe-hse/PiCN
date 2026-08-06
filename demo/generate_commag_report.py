# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause
#
# Redistribution and use under the BSD 3-Clause License; see the LICENSE
# file in the project root for the full text.

"""Run ComMag evaluation campaigns and write figures + markdown report."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from agentic.benchmark.sweep import SweepSpec, load_jsonl, run_sweep
from demo.run_paired_bus import run_paired

_ROOT = Path(__file__).resolve().parent
_DEFAULT_RESULTS = _ROOT / "results" / "commag_eval"
_DEFAULT_REPORT_DIR = _ROOT / "report"


def _mean(vals: list[float]) -> float:
    return statistics.mean(vals) if vals else float("nan")


def _median(vals: list[float]) -> float:
    return statistics.median(vals) if vals else float("nan")


def _stdev(vals: list[float]) -> float:
    return statistics.stdev(vals) if len(vals) > 1 else 0.0


def _trim_cold_start(vals: list[float], *, cap_ms: float = 500.0) -> list[float]:
    """Drop rare SimulationBus cold-start outliers (multi-second stalls)."""
    kept = [v for v in vals if v <= cap_ms]
    return kept if kept else vals


def _by_k(records: list[dict[str, Any]], key: str) -> dict[int, list[float]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for rec in records:
        k = int(rec["config"]["k"])
        value = rec.get("metrics", {}).get(key)
        if value is None and key.startswith("bus."):
            bus_key = key.split(".", 1)[1]
            value = rec.get("bus", {}).get(bus_key)
        if value is None:
            continue
        grouped[k].append(float(value))
    return dict(sorted(grouped.items()))


def _bus_by_k(records: list[dict[str, Any]], bus_key: str) -> dict[int, list[float]]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for rec in records:
        k = int(rec["config"]["k"])
        value = rec.get("bus", {}).get(bus_key)
        if value is None:
            continue
        grouped[k].append(float(value))
    return dict(sorted(grouped.items()))


def _plot_series(
    out: Path,
    series: dict[str, dict[int, list[float]]],
    *,
    title: str,
    ylabel: str,
    xlabel: str = "k (hospitals / fan-out)",
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    for label, grouped in series.items():
        if not grouped:
            continue
        ks = list(grouped.keys())
        means = [_median(_trim_cold_start(grouped[k])) for k in ks]
        errs = [_stdev(_trim_cold_start(grouped[k])) for k in ks]
        ax.errorbar(ks, means, yerr=errs, marker="o", capsize=3, label=label)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    if len(series) > 1:
        ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def _plot_bar_ratios(
    out: Path,
    sync_ratios: list[float],
    async_ratios: list[float],
    *,
    title: str,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5.2, 3.6))
    labels = ["vs NFN sync", "vs NFN async"]
    sync_t = _trim_cold_start(sync_ratios, cap_ms=10.0)  # ratios near O(1)
    async_t = _trim_cold_start(async_ratios, cap_ms=10.0)
    # Ratios are dimensionless; keep all finite positive samples.
    sync_t = [v for v in sync_ratios if 0 < v < 10]
    async_t = [v for v in async_ratios if 0 < v < 10]
    means = [_median(sync_t), _median(async_t)]
    errs = [_stdev(sync_t), _stdev(async_t)]
    ax.bar(labels, means, yerr=errs, capsize=4, color=["#4C78A8", "#F58518"])
    ax.axhline(1.0, color="gray", linestyle="--", linewidth=1, label="parity")
    ax.set_ylabel("Latency ratio (Agentic / NFN)")
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


async def _run_campaigns(
    *,
    results_dir: Path,
    seeds_phase1: list[int],
    seeds_phase2: list[int],
    seeds_network: list[int],
    k_phase1: list[int],
    k_phase2: list[int],
    k_network: list[int],
) -> tuple[Path, Path, Path]:
    from demo.run_cardiac_bus import run_cardiac_network_sweep

    results_dir.mkdir(parents=True, exist_ok=True)
    phase1 = results_dir / "phase1_structural.jsonl"
    phase2 = results_dir / "phase2_paired.jsonl"
    network = results_dir / "cardiac_network.jsonl"

    if phase1.exists() and sum(1 for _ in phase1.open()) >= len(seeds_phase1) * len(
        k_phase1
    ) * 2:
        print(f"Cardiac structural already complete ({phase1}); reusing")
    else:
        if phase1.exists():
            phase1.unlink()
        print(f"Cardiac structural sweep → {phase1}")
        await run_sweep(
            SweepSpec(
                seeds=seeds_phase1,
                k_values=k_phase1,
                paths=("happy", "adversary"),
                transport="bus",
                allow_dirty=True,
                executor_workers=32,
                simulated_interfaces=0,
            ),
            out_path=phase1,
        )

    if phase2.exists():
        phase2.unlink()
    print(f"NFN stack-overhead paired bus → {phase2}")
    await run_paired(
        seeds=seeds_phase2,
        k_values=k_phase2,
        out_path=phase2,
        baseline="nfn_async",
        include_sync=True,
        include_async=True,
    )

    if network.exists():
        network.unlink()
    print(f"Cardiac network (capability Interests) → {network}")
    await run_cardiac_network_sweep(
        seeds=seeds_network,
        k_values=k_network,
        require_paediatric=True,
        log_level=255,
        out_path=network,
    )
    return phase1, phase2, network


def _roll(vals: list[float]) -> dict[str, float]:
    return {
        "n": float(len(vals)),
        "mean": _mean(vals),
        "median": _median(vals),
        "stdev": _stdev(vals),
        "min": min(vals) if vals else float("nan"),
        "max": max(vals) if vals else float("nan"),
    }


def _summarize(
    phase1: list[dict[str, Any]],
    phase2: list[dict[str, Any]],
    network: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from demo.run_cardiac_bus import summarize_cardiac_network_records

    happy = [r for r in phase1 if r["config"]["path"] == "happy"]
    adv = [r for r in phase1 if r["config"]["path"] == "adversary"]
    m4 = [
        float(r["metrics"]["m4_detection_rate"])
        for r in adv
        if r.get("metrics", {}).get("m4_detection_rate") is not None
    ]
    nfn_sync_vals = _trim_cold_start(
        [float(r["bus"]["nfn_sync_elapsed_ms"]) for r in phase2]
    )
    nfn_async_vals = _trim_cold_start(
        [float(r["bus"]["nfn_async_elapsed_ms"]) for r in phase2]
    )
    agentic_vals = _trim_cold_start(
        [float(r["bus"]["agentic_async_elapsed_ms"]) for r in phase2]
    )
    m3_async_raw = [
        float(r["metrics"]["m3_vs_nfn_async"])
        for r in phase2
        if r.get("metrics", {}).get("m3_vs_nfn_async") is not None
    ]
    m3_sync_raw = [
        float(r["metrics"]["m3_vs_nfn_sync"])
        for r in phase2
        if r.get("metrics", {}).get("m3_vs_nfn_sync") is not None
    ]
    m3_async = [v for v in m3_async_raw if 0 < v < 10]
    m3_sync = [v for v in m3_sync_raw if 0 < v < 10]

    def _by_k_roll(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, float]]:
        return {
            str(k): _roll(vals) for k, vals in _by_k(records, key).items()
        }

    network_summary = (
        summarize_cardiac_network_records(network) if network else None
    )

    return {
        "phase1_runs": len(phase1),
        "phase2_runs": len(phase2),
        "network_runs": len(network or []),
        "happy_runs": len(happy),
        "adversary_runs": len(adv),
        "m4_mean": _mean(m4),
        "m4_stdev": _stdev(m4),
        "m4_n": len(m4),
        "m3_vs_async_mean": _mean(m3_async),
        "m3_vs_async_median": _median(m3_async),
        "m3_vs_async_stdev": _stdev(m3_async),
        "m3_vs_sync_mean": _mean(m3_sync),
        "m3_vs_sync_median": _median(m3_sync),
        "m3_vs_sync_stdev": _stdev(m3_sync),
        "nfn_sync_ms": _roll(nfn_sync_vals),
        "nfn_async_ms": _roll(nfn_async_vals),
        "agentic_ms": _roll(agentic_vals),
        "nfn_sync_ms_mean": _mean(nfn_sync_vals),
        "nfn_sync_ms_median": _median(nfn_sync_vals),
        "nfn_sync_ms_stdev": _stdev(nfn_sync_vals),
        "nfn_async_ms_mean": _mean(nfn_async_vals),
        "nfn_async_ms_median": _median(nfn_async_vals),
        "nfn_async_ms_stdev": _stdev(nfn_async_vals),
        "agentic_ms_mean": _mean(agentic_vals),
        "agentic_ms_median": _median(agentic_vals),
        "agentic_ms_stdev": _stdev(agentic_vals),
        "cold_start_note": (
            "Absolute latencies report median after dropping samples >500 ms "
            "(SimulationBus cold-start / teardown stalls). Stdev uses the "
            "same trimmed sample set (sample standard deviation)."
        ),
        "dispatch_by_k": {
            str(k): _mean(v)
            for k, v in _by_k(happy, "dispatch_count").items()
        },
        "dispatch_by_k_stats": _by_k_roll(happy, "dispatch_count"),
        "pit_peak_by_k": {
            str(k): _mean(v)
            for k, v in _by_k(happy, "context_pit_peak").items()
        },
        "pit_peak_by_k_stats": _by_k_roll(happy, "context_pit_peak"),
        "artefact_by_k": {
            str(k): _mean(v)
            for k, v in _by_k(happy, "artefact_bytes_total").items()
        },
        "artefact_by_k_stats": _by_k_roll(happy, "artefact_bytes_total"),
        "cardiac_network": network_summary,
    }


def _write_figures(
    phase1: list[dict[str, Any]],
    phase2: list[dict[str, Any]],
    fig_dir: Path,
    network: list[dict[str, Any]] | None = None,
) -> list[Path]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    happy = [r for r in phase1 if r["config"]["path"] == "happy"]

    p = fig_dir / "fig1_bus_latency_vs_k.png"
    _plot_series(
        p,
        {
            "NFN sync": _bus_by_k(phase2, "nfn_sync_elapsed_ms"),
            "NFN async": _bus_by_k(phase2, "nfn_async_elapsed_ms"),
            "Agentic async": _bus_by_k(phase2, "agentic_async_elapsed_ms"),
        },
        title="NFN stack-overhead: SimulationBus latency (median ± stdev)",
        ylabel="Latency (ms)",
    )
    written.append(p)

    p = fig_dir / "fig2_m3_overhead_ratios.png"
    _plot_bar_ratios(
        p,
        [
            float(r["metrics"]["m3_vs_nfn_sync"])
            for r in phase2
            if r["metrics"].get("m3_vs_nfn_sync") is not None
        ],
        [
            float(r["metrics"]["m3_vs_nfn_async"])
            for r in phase2
            if r["metrics"].get("m3_vs_nfn_async") is not None
        ],
        title="NFN stack-overhead ratio: Agentic / NFN (median ± stdev)",
    )
    written.append(p)

    p = fig_dir / "fig3_dispatch_vs_k.png"
    _plot_series(
        p,
        {"Happy path": _by_k(happy, "dispatch_count")},
        title="Cardiac structural: sub-intent dispatches vs k (median ± stdev)",
        ylabel="Dispatch count",
    )
    written.append(p)

    p = fig_dir / "fig4_artefact_bytes_vs_k.png"
    _plot_series(
        p,
        {"Happy path": _by_k(happy, "artefact_bytes_total")},
        title="Cardiac structural: artefact bytes vs k (median ± stdev)",
        ylabel="Artefact bytes",
    )
    written.append(p)

    p = fig_dir / "fig5_pit_peak_vs_k.png"
    _plot_series(
        p,
        {"Happy path": _by_k(happy, "context_pit_peak")},
        title="Cardiac structural: Context PIT peak vs k (median ± stdev)",
        ylabel="Context PIT peak entries",
    )
    written.append(p)

    if network:
        grouped: dict[int, list[float]] = defaultdict(list)
        for rec in network:
            grouped[int(rec["config"]["k"])].append(float(rec["bus"]["elapsed_ms"]))
        p = fig_dir / "fig6_cardiac_network_latency_vs_k.png"
        _plot_series(
            p,
            {"Cardiac network (/cap/fwd)": dict(sorted(grouped.items()))},
            title="Cardiac network: capability Interest latency (median ± stdev)",
            ylabel="Latency (ms)",
        )
        written.append(p)

    return written


def _write_report(
    *,
    report_path: Path,
    summary: dict[str, Any],
    fig_names: list[str],
    seeds_phase1: list[int],
    seeds_phase2: list[int],
    seeds_network: list[int],
    k_phase1: list[int],
    k_phase2: list[int],
    k_network: list[int],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# ComMag evaluation note — Agentic vs plain NFN (prototype)",
        "",
        f"_Generated {ts}. Prototype-scale, synthetic scenario on PiCN "
        f"`SimulationBus`. Language is deliberately calibrated for an IEEE "
        f"Communications Magazine revision (illustrative, not validation)._ ",
        "",
        "## 1. Scope and framing",
        "",
        "This note accompanies a working prototype of capability-oriented "
        "forwarding beside Named Function Networking (NFN) on a modernized "
        "PiCN stack (Python 3.14, asyncio). It reports **three complementary "
        "campaigns**:",
        "",
        "1. **Cardiac structural (in-process)** — mechanisms of the "
        "agentic layer (Context PIT, dispatch/aggregation, artefact sizes, "
        "adversary KP-A detection) at small hospital counts `k`.",
        "2. **Cardiac network demo (SimulationBus)** — ambulance "
        "`submit_intent` emits `/cap/fwd/...` capability Interests; edge "
        "`AgenticForwarder` returns hospital capacity Content; ambulance "
        "aggregates and ranks (paper §usecase wire story).",
        "3. **NFN stack-overhead (SimulationBus)** — the **same NFN combine "
        "interest** under `NFNForwarder` sync/async and `AgenticForwarder` "
        "async (AgenticLayer idle for that name).",
        "",
        "Numbers below are **preliminary** and must be read with the "
        "comparison and scale stated explicitly. Absolute SimulationBus "
        "wall-clock latency is **not** a deployment figure. The "
        "**Agentic / NFN latency ratio** (legacy id M3; prefer "
        "**nfn_stack_overhead**) on the shared combine interest is a "
        "microbenchmark, **not** cardiac capability routing.",
        "",
        "## 2. Exact setup (read this first)",
        "",
        "Do not mix the three campaigns. Capability maps become "
        "`/cap/fwd/...` Interests — they are **not** rewritten as NFN "
        "λ-names (paper CH1 is future work).",
        "",
        "### 2.1 NFN stack-overhead — SimulationBus NFN combine",
        "",
        "This is what produces the **with vs without agentic** latency "
        "tables and the publishable stack-overhead ratio.",
        "",
        "**Who sends what.** A PiCN `Fetch` client issues one Interest and "
        "waits for Content. Nobody sends cardiac / capability Interests on "
        "the bus in this campaign.",
        "",
        "**Exact name (Interest).** Built by `nfn_combine_interest(k)`:",
        "",
        "```text",
        "/func/combine/_(\"h0\",\"h1\")/NFN                    # k_eff=2",
        "/func/combine/_(_(\"h0\",\"h1\"),\"h2\")/NFN            # k_eff=3",
        "/func/combine/_(_(_(\"h0\",\"h1\"),\"h2\"),\"h3\")/NFN  # k_eff=4",
        "```",
        "",
        "Nesting depth is capped at 4 (`k_eff = min(k, 4)`); deeper nests "
        "hang on sync NFN under SimulationBus. The last component is the "
        "NFN marker, so this is a **classic NFN computation name**, not a "
        "capability name such as `/cap/fwd/hospital/beds/...`.",
        "",
        "**Where it is sent.** `Fetch` is attached to the primary "
        "forwarder's SimulationBus face (e.g. `nfn0` or `agentic0`). The "
        "Interest travels: Fetch → bus → primary node link layer → up the "
        "stack. Mgmt on the primary node installs:",
        "",
        "- a face toward the companion node,",
        "- FIB prefix `/func` → that face,",
        "- CS content `/func/combine` = Python "
        "`def func(a, b): return str(a) + str(b)`.",
        "",
        "**Nodes per strategy (always 3 faces on one `SimulationBus`).**",
        "",
        "| Strategy | Node A (primary) | Node B | Client |",
        "|----------|------------------|--------|--------|",
        "| `nfn_sync` / `nfn_async` | `NFNForwarder` | `NFNForwarder` | `Fetch` |",
        "| `agentic_async` | `AgenticForwarder` | `NFNForwarder` | `Fetch` |",
        "",
        "So: **two forwarders + one fetch client**. Only the "
        "`agentic_async` primary runs an `AgenticLayer`. The companion is "
        "always a plain `NFNForwarder` (async when the primary is async).",
        "",
        "**Does the Interest go through NFN or bypass it?** It **goes "
        "through NFN** and is executed there. Stack on "
        "`AgenticForwarder` (top → bottom):",
        "",
        "```text",
        "AgenticLayer",
        "NFNLayer          ← claims names ending in /NFN; runs combine",
        "Chunk / Timeout / ICN / PacketEncoding / Link",
        "```",
        "",
        "NFN claims the combine Interest; `AgenticLayer` is **present in "
        "the stack** but does **not** perform capability matching or "
        "Context-PIT decomposition for this name. The ratio therefore "
        "measures \"NFN fetch with an idle AgenticLayer above NFN\" vs "
        "\"plain NFNForwarder\", **not** cardiac capability routing.",
        "",
        "### 2.2 Cardiac network demo — capability Interests on SimulationBus",
        "",
        "Paper-aligned wire story (`python -m demo.run_cardiac_bus`):",
        "",
        "```text",
        "ambulance PicnSubstratePort + AgenticLayer.submit_intent",
        "  → Interest /cap/fwd/enrichment",
        "  → Interest /cap/fwd/hospital/beds/h0  (…/h1 …)",
        "  → Interest /cap/fwd/traffic",
        "  → Interest /cap/fwd/ranking",
        "edge AgenticForwarder CS answers with Content",
        "ambulance aggregates Context~PIT trace root and ranks hospitals",
        "```",
        "",
        "Hospital capacity is published as distinct capability Content "
        "names (optional `paediatric_team` claim). This campaign does "
        "**not** use NFN λ-names.",
        "",
        "### 2.3 Cardiac structural (in-process agentic)",
        "",
        "This is what produces dispatch / PIT / artefact-byte figures and "
        "adversary M4.",
        "",
        "**Scenario (who sends what).** An abstract ambulance intake "
        "orchestrator (Python, `CardiacScenario`) commits a parent intent, "
        "then \"forwards\" sub-intents to:",
        "",
        "- 1 enrichment leaf,",
        "- **`k` hospital** capability producers (`hospital/beds`, "
        "`DeterministicBackend` returning `beds_free`),",
        "- 1 traffic leaf,",
        "- 1 ranking leaf.",
        "",
        "Hospitals return signed capacity claims (attestation quotes). "
        "Aggregation builds a Merkle **trace root**. On the adversary "
        "path, one hospital advertises inflated beds; KP-A compares "
        "claims to world truth and **deprioritises** (does not ban) that "
        "hospital.",
        "",
        "**Not on SimulationBus.** No PiCN Interest/Content is exchanged "
        "for these leaves in the structural campaign. Context PIT / "
        "reputation / accountability run in-process. Harness "
        "`transport=bus` is only metadata for A-011 gates.",
        "",
        "### 2.4 How we extract agentic numbers without mgmt APIs",
        "",
        "Stock `AsyncMgmt` only sees CS / FIB / PIT / faces — **not** "
        "C-FIB, Context PIT, Steer, or reputation (future agentic mgmt "
        "surface). Numbers come from the **Python API**:",
        "",
        "| Quantity | Source |",
        "|----------|--------|",
        "| NFN stack-overhead latency | `time.perf_counter` around "
        "`Fetch.fetch_data[_async]` |",
        "| Context PIT peak, dispatch count, aggregation count, "
        "artefact bytes | `CardiacScenario` return dict → harness "
        "`MetricEvent`s |",
        "| M4 detection | adversary artefacts `kpa_mismatches` |",
        "| Trace root / ranking | walkthrough / scenario artefacts |",
        "| Cardiac network Interests | `demo.run_cardiac_bus` / "
        "`RecordingPicnSubstratePort` |",
        "",
        "So we instrument **inside** the scenario/harness, not by scraping "
        "HTTP mgmt.",
        "",
        "### 2.5 How an agentic intent becomes routable names "
        "(not NFN lambdas)",
        "",
        "A common mental model is: JSON intent → decomposer → NFN λ-expression "
        "names. **That is not how this prototype works.** Agentic routing and "
        "NFN computation use **different name spaces**.",
        "",
        "#### Agentic path (capability names)",
        "",
        "1. **Parent intent attributes** (for template selection) are a small "
        "JSON-like map, e.g. `{\"scenario\": \"cardiac\"}`. They are **not** "
        "the Interest name.",
        "2. A signed **task-graph template** (JCS JSON body, kind "
        "`task-graph-template`) describes operators `SEQ` / `PAR` / `ALT` / "
        "`LEAF`. Each `LEAF` carries a **capability path**, e.g. "
        "`[\"hospital\", \"beds\"]` — not an NFN expression.",
        "3. `BoundedDecomposer.decompose(intent_attrs, bindings)` matches "
        "predicates and emits a deterministic list of `SubIntent` objects "
        "`(index, capability, bindings)`. Example emission for a PAR of "
        "eta / rank / beds:",
        "",
        "```text",
        "SubIntent(0, capability=(\"eta\",), …)",
        "SubIntent(1, capability=(\"rank\",), …)",
        "SubIntent(2, capability=(\"hospital\", \"beds\"), …)",
        "```",
        "",
        "4. `AgenticLayer.submit_intent(...)` commits those leaves to the "
        "Context PIT, then for each leaf calls the substrate with:",
        "",
        "```text",
        "Name:    /cap/fwd/<capability-path…>     # G.1 simplified wire form",
        "         # Full public form (naming module):",
        "         # /cap/<issuer-digest>/<path…>/v=<version>",
        "Payload: JSON bytes (e.g. {\"patient_id\": \"demo\"})",
        "```",
        "",
        "5. Those Interests are **capability Interests**. On a PiCN stack "
        "they travel **past** NFN (NFN only claims names ending in the "
        "`NFN` marker) up to `AgenticLayer` / producers. They are **not** "
        "rewritten into `/func/…/_(\"a\",/data/x)/NFN` lambdas.",
        "",
        "#### NFN path (lambda / combine names) — separate",
        "",
        "NFN names look like:",
        "",
        "```text",
        "/func/combine/_(\"h0\",\"h1\")/NFN",
        "```",
        "",
        "The **NFN stack-overhead** campaign uses **only** that NFN form "
        "(Fetch → combine). No decomposer, no `/cap/…` names, no JSON intent "
        "body for routing. **Cardiac structural** metrics use Context PIT + "
        "capability paths **in-process** (and currently hard-code leaf specs "
        "rather than calling `BoundedDecomposer` on every run). "
        "**Cardiac network** puts `/cap/fwd/...` Interests on SimulationBus.",
        "",
        "```text",
        "JSON intent attrs ──► BoundedDecomposer ──► SubIntent(capability path)",
        "                                            │",
        "                                            ▼",
        "                              /cap/…/hospital/beds/v=…  + JSON payload",
        "                              (capability routing / C-FIB)",
        "",
        "                    ≠",
        "",
        "                     /func/combine/_(…)/NFN",
        "                     (NFN executor; stack-overhead microbenchmark)",
        "```",
        "",
        "## 3. Configuration",
        "",
        f"| Campaign | Seeds | `k` values | Paths / strategies |",
        f"|----------|-------|------------|--------------------|",
        f"| Cardiac structural | `{seeds_phase1}` | `{k_phase1}` | "
        f"happy, adversary |",
        f"| NFN stack-overhead | `{seeds_phase2}` | `{k_phase2}` | "
        f"nfn_sync, nfn_async, agentic_async |",
        f"| Cardiac network demo | `{seeds_network}` | `{k_network}` | "
        f"`/cap/fwd` Interests |",
        "",
        f"- Cardiac structural runs: **{summary['phase1_runs']}** "
        f"(happy={summary['happy_runs']}, adversary={summary['adversary_runs']})",
        f"- NFN stack-overhead paired comparisons: **{summary['phase2_runs']}**",
        f"- Cardiac network runs: **{summary.get('network_runs', 0)}**",
        "- Agents: `DeterministicBackend` only (no live LLM; AC3).",
        "- Transport: PiCN `SimulationBus` for network campaigns; "
        "structural harness label `transport=bus` (in-process mechanisms).",
        "- **Variance**: all multi-run tables report sample standard "
        "deviation (stdev) alongside mean/median.",
        "",
        "## 4. Results — NFN stack-overhead (SimulationBus)",
        "",
        "Fetch latency for the identical nested `combine` interest "
        "(trimmed >500 ms cold-start stalls). **Mean ± stdev** and median:",
        "",
        f"| Strategy | Median (ms) | Mean ± stdev (ms) | n |",
        f"|----------|-------------|-------------------|---|",
        f"| NFNForwarder sync | "
        f"{summary['nfn_sync_ms_median']:.3f} | "
        f"{summary['nfn_sync_ms_mean']:.3f} ± "
        f"{summary['nfn_sync_ms_stdev']:.3f} | "
        f"{int(summary['nfn_sync_ms']['n'])} |",
        f"| NFNForwarder async | "
        f"{summary['nfn_async_ms_median']:.3f} | "
        f"{summary['nfn_async_ms_mean']:.3f} ± "
        f"{summary['nfn_async_ms_stdev']:.3f} | "
        f"{int(summary['nfn_async_ms']['n'])} |",
        f"| AgenticForwarder async | "
        f"{summary['agentic_ms_median']:.3f} | "
        f"{summary['agentic_ms_mean']:.3f} ± "
        f"{summary['agentic_ms_stdev']:.3f} | "
        f"{int(summary['agentic_ms']['n'])} |",
        "",
        f"_{summary['cold_start_note']}_",
        "",
        "### NFN stack-overhead ratios (publishable; legacy id M3)",
        "",
        f"| Comparison | Median | Mean ± stdev |",
        f"|------------|--------|--------------|",
        f"| Agentic / NFN async | "
        f"**{summary['m3_vs_async_median']:.3f}** | "
        f"{summary['m3_vs_async_mean']:.3f} ± "
        f"{summary['m3_vs_async_stdev']:.3f} |",
        f"| Agentic / NFN sync | "
        f"{summary['m3_vs_sync_median']:.3f} | "
        f"{summary['m3_vs_sync_mean']:.3f} ± "
        f"{summary['m3_vs_sync_stdev']:.3f} |",
        "",
        "A median ratio near **1.0** against async NFN suggests that, at this "
        "prototype scale and for this **NFN combine** workload, placing "
        "`AgenticLayer` above NFN does not introduce large additional "
        "fetch latency beyond the async NFN baseline. Stdev quantifies "
        "run-to-run fluctuation on SimulationBus.",
        "",
        f"![Bus latency vs k](figures/{fig_names[0]})",
        "",
        f"![NFN stack-overhead ratios](figures/{fig_names[1]})",
        "",
        "## 4b. Results — cardiac network (capability Interests)",
        "",
        "Ambulance `submit_intent` → `/cap/fwd/...` Interests on "
        "SimulationBus → edge CS Content → Context~PIT aggregation. "
        f"Runs: **{summary.get('network_runs', 0)}** "
        f"(seeds `{seeds_network}`, k `{k_network}`).",
        "",
    ]
    cn = summary.get("cardiac_network") or {}
    if cn:
        e = cn.get("elapsed_ms") or {}
        lines.extend(
            [
                f"| Metric | Median | Mean ± stdev | n |",
                f"|--------|--------|--------------|---|",
                f"| elapsed_ms | {e.get('median', float('nan')):.3f} | "
                f"{e.get('mean', float('nan')):.3f} ± "
                f"{e.get('stdev', float('nan')):.3f} | "
                f"{int(e.get('n', 0))} |",
                "",
                "Per-`k` capability-Interest latency:",
                "",
                "| k | n | Median (ms) | Mean ± stdev (ms) |",
                "|---|---|-------------|-------------------|",
            ]
        )
        for k, roll in (cn.get("elapsed_ms_by_k") or {}).items():
            lines.append(
                f"| {k} | {int(roll['n'])} | {roll['median']:.3f} | "
                f"{roll['mean']:.3f} ± {roll['stdev']:.3f} |"
            )
        if len(fig_names) > 5:
            lines.extend(
                [
                    "",
                    f"![Cardiac network latency vs k](figures/{fig_names[5]})",
                ]
            )
    lines.extend(
        [
            "",
            "## 5. Results — cardiac structural (in-process)",
            "",
            "In-process agentic mechanisms scale with `k`. Values are "
            "**mean ± stdev** across seeds (happy path).",
            "",
            "| k | Dispatch (mean ± stdev) | PIT peak (mean ± stdev) | "
            "Artefact bytes (mean ± stdev) |",
            "|---|-------------------------|-------------------------|------"
            "--------------------------|",
        ]
    )
    for k in sorted(int(x) for x in summary["dispatch_by_k"]):
        ks = str(k)
        d = summary.get("dispatch_by_k_stats", {}).get(ks) or {
            "mean": summary["dispatch_by_k"][ks],
            "stdev": 0.0,
        }
        p = summary.get("pit_peak_by_k_stats", {}).get(ks) or {
            "mean": summary["pit_peak_by_k"].get(ks, float("nan")),
            "stdev": 0.0,
        }
        a = summary.get("artefact_by_k_stats", {}).get(ks) or {
            "mean": summary["artefact_by_k"].get(ks, float("nan")),
            "stdev": 0.0,
        }
        lines.append(
            f"| {k} | {d['mean']:.1f} ± {d['stdev']:.2f} | "
            f"{p['mean']:.1f} ± {p['stdev']:.2f} | "
            f"{a['mean']:.0f} ± {a['stdev']:.1f} |"
        )
    lines.extend(
        [
            "",
            f"Adversary KP-A detection rate (M4): "
            f"**{summary['m4_mean']:.2f} ± {summary.get('m4_stdev', 0):.2f}** "
            f"over n={summary['m4_n']} adversary runs (prototype oracle).",
            "",
            f"![Dispatch vs k](figures/{fig_names[2]})",
            "",
            f"![Artefact bytes vs k](figures/{fig_names[3]})",
            "",
            f"![Context PIT peak vs k](figures/{fig_names[4]})",
            "",
            "## 6. What a ComMag revision can claim",
            "",
            "Suggested calibrated claims (item-25 style):",
            "",
            "1. **Artifact**: public fork with runnable `demo/` scripts and "
            "tests (cardiac walkthrough; cardiac network bus; NFN "
            "stack-overhead pairing).",
            "2. **Qualifying number**: NFN stack-overhead median ≈ "
            f"**{summary['m3_vs_async_median']:.2f}** "
            f"(mean {summary['m3_vs_async_mean']:.2f} ± "
            f"{summary['m3_vs_async_stdev']:.2f}) "
            "(AgenticForwarder async / NFNForwarder async) for the shared "
            f"combine interest at `k ∈ {k_phase2}`, seeds "
            f"`{seeds_phase2[0]}…{seeds_phase2[-1]}`, SimulationBus, "
            "prototype-scale synthetic scenario.",
            "3. **Mechanism evidence**: cardiac happy/adversary paths "
            "demonstrate Context PIT commit-before-forward, Merkle trace "
            "roots, and KP-A deprioritisation (not ban). Cardiac network "
            "demo shows `/cap/fwd` Interests on SimulationBus.",
            "",
            "Avoid: bare end-to-end latency as a headline; claims of "
            "deployment validation; or describing stack-overhead as full "
            "cardiac-over-bus leaf routing (use `run_cardiac_bus` for that).",
            "",
            "## 7. How to reproduce",
            "",
            "```bash",
            "pip install -e \".[dev]\" matplotlib",
            "python -m demo.run_cardiac_bus --seeds 1-5 --k-list 2,3,4 \\",
            "  --out demo/results/cardiac_network.jsonl --replace-out \\",
            "  --summary-json demo/results/cardiac_network_summary.json",
            "python -m demo.generate_commag_report",
            "```",
            "",
            "Raw JSONL: `demo/results/commag_eval/`. Figures and this "
            "report: `demo/report/`.",
            "",
            "## 8. References (in-repo)",
            "",
            "- [`docs/agentic_demo.md`](../../docs/agentic_demo.md)",
            "- [`demo/README.md`](../README.md)",
            "- [`docs/agentic.md`](../../docs/agentic.md)",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    results_dir = Path(args.results_dir)
    report_dir = Path(args.report_dir)
    fig_dir = report_dir / "figures"

    seeds_p1 = list(range(args.seed_start, args.seed_start + args.n_seeds_phase1))
    seeds_p2 = list(range(args.seed_start, args.seed_start + args.n_seeds_phase2))
    seeds_net = list(
        range(args.seed_start, args.seed_start + args.n_seeds_network)
    )
    k_p1 = [int(x) for x in args.k_phase1.split(",")]
    k_p2 = [int(x) for x in args.k_phase2.split(",")]
    k_net = [int(x) for x in args.k_network.split(",")]

    if args.skip_runs:
        phase1_path = results_dir / "phase1_structural.jsonl"
        phase2_path = results_dir / "phase2_paired.jsonl"
        network_path = results_dir / "cardiac_network.jsonl"
    else:
        phase1_path, phase2_path, network_path = await _run_campaigns(
            results_dir=results_dir,
            seeds_phase1=seeds_p1,
            seeds_phase2=seeds_p2,
            seeds_network=seeds_net,
            k_phase1=k_p1,
            k_phase2=k_p2,
            k_network=k_net,
        )

    phase1 = load_jsonl(phase1_path)
    phase2 = load_jsonl(phase2_path)
    network = load_jsonl(network_path) if network_path.exists() else []
    summary = _summarize(phase1, phase2, network)
    report_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    figs = _write_figures(phase1, phase2, fig_dir, network)
    fig_names = [p.name for p in figs]
    report_path = report_dir / "commag_evaluation.md"
    _write_report(
        report_path=report_path,
        summary=summary,
        fig_names=fig_names,
        seeds_phase1=seeds_p1,
        seeds_phase2=seeds_p2,
        seeds_network=seeds_net,
        k_phase1=k_p1,
        k_phase2=k_p2,
        k_network=k_net,
    )
    print(f"Wrote report {report_path}")
    print(f"Wrote {len(figs)} figures under {fig_dir}")
    print(
        f"NFN stack-overhead vs async: median={summary['m3_vs_async_median']:.3f} "
        f"mean={summary['m3_vs_async_mean']:.3f} "
        f"± {summary['m3_vs_async_stdev']:.3f}"
    )
    cn = summary.get("cardiac_network") or {}
    if cn:
        e = cn.get("elapsed_ms") or {}
        print(
            f"Cardiac network elapsed_ms: median={e.get('median', float('nan')):.3f} "
            f"mean={e.get('mean', float('nan')):.3f} "
            f"± {e.get('stdev', float('nan')):.3f} (n={int(e.get('n', 0))})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ComMag evaluation runs + matplotlib figures + markdown report"
    )
    parser.add_argument(
        "--results-dir",
        default=str(_DEFAULT_RESULTS),
        help="JSONL output directory",
    )
    parser.add_argument(
        "--report-dir",
        default=str(_DEFAULT_REPORT_DIR),
        help="Markdown report + figures directory",
    )
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--n-seeds-phase1", type=int, default=5)
    parser.add_argument("--n-seeds-phase2", type=int, default=10)
    parser.add_argument("--n-seeds-network", type=int, default=5)
    parser.add_argument("--k-phase1", default="2,3,4,5,8")
    parser.add_argument("--k-phase2", default="2,3,4")
    parser.add_argument("--k-network", default="2,3,4")
    parser.add_argument(
        "--skip-runs",
        action="store_true",
        help="Reuse existing JSONL; only regenerate figures/report",
    )
    args = parser.parse_args(argv)
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
