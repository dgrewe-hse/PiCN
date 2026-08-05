# ComMag evaluation note — Agentic vs plain NFN (prototype)

_Generated 2026-08-05 18:45 UTC. Prototype-scale, synthetic scenario on PiCN `SimulationBus`. Language is deliberately calibrated for an IEEE Communications Magazine revision (illustrative, not validation)._ 

## 1. Scope and framing

This note accompanies a working prototype of capability-oriented forwarding beside Named Function Networking (NFN) on a modernized PiCN stack (Python 3.14, asyncio). It reports **two complementary campaigns**:

1. **Structural cardiac campaign (in-process)** — mechanisms of the agentic layer (Context PIT, dispatch/aggregation, artefact sizes, adversary KP-A detection) at small hospital counts `k`.
2. **SimulationBus paired campaign** — the **same NFN combine interest** under three strategies: `NFNForwarder` sync, `NFNForwarder` async, and `AgenticForwarder` async.

Numbers below are **preliminary** and must be read with the comparison and scale stated explicitly. Absolute SimulationBus wall-clock latency is **not** a deployment figure; the **Agentic / NFN latency ratio (M3)** on the shared combine interest is the primary ComMag-facing candidate.

## 2. Configuration

| Campaign | Seeds | `k` values | Paths / strategies |
|----------|-------|------------|--------------------|
| Phase 1 structural | `[1, 2, 3, 4, 5]` | `[2, 3, 4, 5, 8]` | happy, adversary |
| Phase 2 paired bus | `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` | `[2, 3, 4]` | nfn_sync, nfn_async, agentic_async |

- Phase 1 runs: **50** (happy=25, adversary=25)
- Phase 2 paired comparisons: **30**
- Agents: `DeterministicBackend` only (no live LLM; AC3).
- Transport: PiCN `SimulationBus` for Phase 2; Phase 1 harness label `transport=bus` (in-process mechanisms).

## 3. Results — with vs without the agentic layer (SimulationBus)

Mean fetch latency for the identical nested `combine` interest (median after dropping >500 ms cold-start stalls):

| Strategy | Median latency (ms) | Mean (trimmed) |
|----------|---------------------|----------------|
| NFNForwarder sync (without agentic) | 23.780 | 27.069 |
| NFNForwarder async (without agentic) | 8.882 | 9.001 |
| AgenticForwarder async (with agentic layer) | 7.224 | 7.591 |

_Absolute latencies report median after dropping samples >500 ms (SimulationBus cold-start / teardown stalls)._

### M3 overhead ratios (publishable)

| Comparison | Median ratio | Mean | Stdev |
|------------|--------------|------|-------|
| Agentic / NFN async | **0.910** | 0.914 | 0.156 |
| Agentic / NFN sync | 0.334 | 0.350 | 0.096 |

A median ratio near **1.0** against async NFN suggests that, at this prototype scale and for this combine workload, stacking `AgenticLayer` above NFN does not introduce large additional fetch latency beyond the async NFN baseline. The sync NFN baseline is a different runtime (multiprocessing layers) and is reported for completeness; cross-runtime absolute times should not be over-interpreted.

![Bus latency vs k](figures/fig1_bus_latency_vs_k.png)

![M3 overhead ratios](figures/fig2_m3_overhead_ratios.png)

## 4. Results — agentic structural mechanisms (cardiac)

These figures illustrate that the agentic mechanisms scale with `k` in the expected direction (more hospital leaves → more dispatches and artefact bytes). They are **not** NFN baselines.

Mean happy-path structural metrics by `k`:

| k | Dispatch count | Context PIT peak | Artefact bytes |
|---|----------------|------------------|----------------|
| 2 | 5.0 | 1.0 | 893 |
| 3 | 6.0 | 1.0 | 1326 |
| 4 | 7.0 | 1.0 | 1757 |
| 5 | 8.0 | 1.0 | 2190 |
| 8 | 11.0 | 1.0 | 3489 |

Adversary KP-A detection rate (M4): **1.00** over n=25 adversary runs (prototype oracle).

![Dispatch vs k](figures/fig3_dispatch_vs_k.png)

![Artefact bytes vs k](figures/fig4_artefact_bytes_vs_k.png)

![Context PIT peak vs k](figures/fig5_pit_peak_vs_k.png)

## 5. What a ComMag revision can claim

Suggested calibrated claims (item-25 style):

1. **Artifact**: public fork with runnable `demo/` scripts and tests (cardiac walkthrough; SimulationBus pairing).
2. **Qualifying number**: M3 median ≈ **0.91** (mean 0.91 ± 0.16) (AgenticForwarder async / NFNForwarder async) for the shared combine interest at `k ∈ [2, 3, 4]`, seeds `1…10`, SimulationBus, prototype-scale synthetic scenario.
3. **Mechanism evidence**: cardiac happy/adversary paths demonstrate Context PIT commit-before-forward, Merkle trace roots, and KP-A deprioritisation (not ban).

Avoid: bare end-to-end latency as a headline; claims of deployment validation; or describing M3 as full cardiac-over-bus leaf routing (that remains future work).

## 6. How to reproduce

```bash
pip install -e ".[dev]" matplotlib
python -m demo.generate_commag_report
```

Raw JSONL: `demo/results/commag_eval/`. Figures and this report: `demo/report/`.

## 7. References (in-repo)

- [`docs/agentic_demo.md`](../../docs/agentic_demo.md)
- [`demo/README.md`](../README.md)
- [`docs/agentic.md`](../../docs/agentic.md)
