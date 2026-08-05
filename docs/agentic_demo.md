# Agentic demo and measurements

ComMag-facing prototype: cardiac-response scenario, measurement campaigns,
SimulationBus pairing of plain NFN vs `AgenticForwarder`, and figure export.

Operator scripts live in the top-level [`demo/`](../demo/) package. Library
code stays under [`agentic/`](../agentic/).

**No live LLM is required.**

## Scenario story

An ambulance intake ranks `k` hospitals for free beds. The agentic path:

1. Commits a Context PIT expected set (enrichment, `k` hospital leaves,
   traffic, ranking) **before** any forward (invariant I2).
2. Collects signed capacity claims (DeterministicBackend — not an LLM).
3. Aggregates responses into a Merkle **trace root**.
4. On the adversary path, a hospital presents a **valid quote with a false
   bed claim**; KP-A compares claims to world truth and **deprioritises**
   (does not ban) the adversary (I8).

## Mocked vs real

| Piece | Phase 1 (in-process) | Phase 2 (SimulationBus) |
|-------|----------------------|-------------------------|
| Hospital agents | `DeterministicBackend` | Same for structural cardiac metrics |
| Substrate | In-process Context PIT / trust | PiCN `SimulationBus` multi-node |
| NFN baseline | — | `NFNForwarder` **sync** and **async** |
| Agentic | — | `AgenticForwarder` **async only** |
| Harness `transport=bus` | Metadata + A-011 gates only | Real bus for paired strategies |
| M3 overhead | **Not publishable** | Publishable after paired runs |

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# optional PNG figures:
pip install matplotlib

python -m demo.cardiac_walkthrough --k 3 --seed 42
```

Expect verified trace roots, ranking, and (adversary) KP-A / reputation lines.

## Building an agentic workflow

Use the agentic layer in three ways. Details and code: [`hello_agent.md`](hello_agent.md).

### 1. Mock substrate (fastest)

`AgenticLayer` + `MockSubstratePort` + `register_capability` +
`DeterministicBackend`. No network, no SimulationBus. Ideal for unit tests and
the hello-agent snippet.

### 2. PiCN UDP / `AgenticForwarder`

```python
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

fwd = AgenticForwarder(port=0, runtime=Runtime.ASYNC, log_level=255)
fwd.register_capability(descriptor, backend)
await fwd.start_forwarder_async()
await fwd.stop_forwarder_async()
```

Async-only. Faces and classic CS/FIB/PIT are managed via stock `AsyncMgmt`
(ICN tables only — not agentic C-FIB / Context PIT).

### 3. PiCN SimulationBus (this demo’s Phase 2)

Multi-node bus with the **same NFN combine interest** under:

* `NFNForwarder` sync · `NFNForwarder` async · `AgenticForwarder` async

```bash
python -m demo.run_paired_bus --seeds 1-3 --k 2,3 --baseline nfn_async \
  --out demo/results/phase2_paired.jsonl --allow-dirty
```

Plain NFN tutorials (no agentic layer): `PiCN/Simulations/SimulationsTutorial.py`
and `SimulationsTutorial_async.py` — see [simulation.md](simulation.md).

## Measurement campaigns (Phase 1)

Default grid ≈ **50 runs**: seeds `1–5` × `k ∈ {2,3,4,5,8}` × `{happy,adversary}`.

```bash
python -m demo.run_sweep --seeds 1-5 --k 2,3,4,5,8 --path happy,adversary \
  --out demo/results/phase1.jsonl --allow-dirty
```

Scale toward ~100 runs by widening `--seeds` (e.g. `1-10`).

JSONL fields (per record): `kind`, `config`, `metadata`, `metrics`,
`artefacts_summary`. Metrics include latency, message/artefact bytes, Context
PIT peak, dispatch count, aggregation completions, and `m3_publishable`
(false in Phase 1).

## Figures

```bash
python -m demo.plot_metrics --in demo/results/phase1.jsonl \
  --out demo/results/figures/
```

Writes CSV always; PNG if `matplotlib` is installed. Structural plots:
latency vs `k`, PIT peak, dispatch count, artefact bytes, M4 detection summary.

### M3 gate

```bash
python -m demo.plot_metrics --in demo/results/phase2_paired.jsonl \
  --out demo/results/figures/ --include-m3
```

`--include-m3` **refuses** unless records have `m3_publishable=true` (real
plain-NFN baselines labelled `mode=plain_nfn`).

## Phase 2 — SimulationBus paired NFN vs Agentic

Same **NFN combine interest** on one PiCN `SimulationBus`, three strategies:

| Strategy | Primary node | Runtime |
|----------|--------------|---------|
| `nfn_sync` | `NFNForwarder` ×2 + `Fetch` | **sync** (multiprocessing layers) |
| `nfn_async` | `NFNForwarder` ×2 + `Fetch` | **async** |
| `agentic_async` | `AgenticForwarder` + companion `NFNForwarder` + `Fetch` | **async only** (A-002) |

```bash
python -m demo.run_paired_bus --seeds 1-10 --k 3 \
  --out demo/results/phase2_paired.jsonl --allow-dirty

# Publishable M3 denominator (default: async NFN, peer of Agentic):
python -m demo.run_paired_bus --seeds 1-5 --k 2,3 --baseline nfn_async
python -m demo.run_paired_bus --seeds 1-5 --k 2,3 --baseline nfn_sync
```

Each JSONL record reports:

* `bus.nfn_sync_elapsed_ms` / `nfn_async_elapsed_ms` / `agentic_async_elapsed_ms`
* `metrics.m3_overhead_ratio` (publishable vs `--baseline`)
* `metrics.m3_vs_nfn_sync` and `metrics.m3_vs_nfn_async` (both ratios)
* Cardiac **structural** metrics from the in-process scenario (co-reported)

Publishable M3 is **AgenticForwarder async / chosen plain NFN** for that shared
combine interest — **not** full cardiac leaf routing over the bus. See
`metadata.m3_note` and `config.workload`.

## Paper metrics map

| Id | Meaning | Publishable notes |
|----|---------|-------------------|
| M1 | End-to-end latency | Absolute bus wall-clock is prototype-scale only |
| M2 | Message / artefact bytes | Structural sizes from scenario artefacts |
| M3 | Agentic / plain-NFN ratio | Only when `m3_publishable` |
| M4 | Adversary detection rate | Adversary path |
| M5 | Latency vs `k` | Scale axis |

Structural extras: `context_pit_peak`, `dispatch_count`,
`aggregation_complete`, `artefact_bytes_total`.

## Reproducibility (A-011)

Harness sweeps require `--seed` / transport metadata and refuse dirty trees
unless `--allow-dirty` (stamps non-reproducible). Capacity gate: bus runs must
not claim more simulated interfaces than executor workers.

## Publishing path

1. Develop on `agentic/commag-demo-measurements`.
2. Merge `demo/` + docs into `agentic/implementation` (default).
3. Optionally cut `agentic/demo` (or a tag) for a stable ComMag artifact URL
   once Phase 2 M3 numbers are honest and documented.

## Tests

```bash
python -m pytest agentic/tests/test_demo_bus_paired.py \
  agentic/tests/test_demo_sweep.py agentic/tests/test_demo_cli.py \
  agentic/tests/test_m3_publishable.py -v --timeout=90

# Full agentic suite + coverage (CI gate: agentic/ ≥ 90%)
python -m pytest agentic/ --timeout=90 --cov=agentic --cov-fail-under=90
```

Demo modules are exercised by the `test_demo_*` tests above (imported from
`agentic/tests/`).

## Related docs

* [Architecture](agentic.md) · [Hello agent](hello_agent.md) ·
  [Config / harness](agentic_config.md) · [Simulation](simulation.md)
* Package entry: [`demo/README.md`](../demo/README.md)
