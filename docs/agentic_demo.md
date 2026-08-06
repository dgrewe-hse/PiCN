# Agentic demo and measurements

ComMag-facing prototype: cardiac-response scenario, measurement campaigns,
SimulationBus capability routing, NFN stack-overhead pairing, and figure export.

Operator scripts live in the top-level [`demo/`](../demo/) package. Library
code stays under [`agentic/`](../agentic/).

**No live LLM is required.**

## Scenario story (IEEE ComMag §usecase)

An ambulance agent issues a signed STEMI intent. Bounded decomposition emits
capability-scoped sub-intents (enrichment, parallel hospital capacity, traffic
ETA, ranking). On the network path those become **`/cap/fwd/...` Interests**
(not NFN λ-expressions). Hospital capacity Content returns; the ambulance
aggregates a Context~PIT Merkle trace root and ranks hospitals (optional
paediatric-team filter). Adversary path: inflated beds → KP-A deprioritises.

## Campaign names (prefer these over “Phase 1 / Phase 2 / M3”)

| Name | What it measures | CLI |
|------|------------------|-----|
| **Cardiac structural** | In-process Context PIT / dispatch / KP-A | `demo.run_sweep`, `demo.cardiac_walkthrough` |
| **Cardiac network demo** | `/cap/fwd` Interests on SimulationBus → aggregate | `demo.run_cardiac_bus` |
| **NFN stack-overhead** | Same `/func/combine/…/NFN` under NFN sync/async vs Agentic async | `demo.run_paired_bus` |

Legacy JSON keys `m3_publishable` / `m3_overhead_ratio` remain; new aliases are
`nfn_stack_overhead_publishable` / `nfn_stack_overhead_ratio`.

## Mocked vs real

| Piece | Cardiac structural | Cardiac network | NFN stack-overhead |
|-------|--------------------|-----------------|--------------------|
| Hospital agents | `DeterministicBackend` | Capability Content under `/cap/fwd/hospital/beds/h*` | N/A (combine only) |
| Substrate | In-process Context PIT | PiCN `SimulationBus` | PiCN `SimulationBus` |
| Wire names | — | `/cap/fwd/...` | `/func/combine/…/NFN` |
| Publishable stack tax | No | No | Yes (`nfn_stack_overhead_*`) |

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip install matplotlib  # optional PNG figures

python -m demo.cardiac_walkthrough --k 3 --seed 42
python -m demo.run_cardiac_bus --k 2 --seed 1
```

## Building an agentic workflow

### 1. Mock substrate (fastest)

`AgenticLayer` + `MockSubstratePort` + `register_capability` +
`DeterministicBackend`. See [hello_agent.md](hello_agent.md).

### 2. PiCN UDP / `AgenticForwarder`

```python
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

fwd = AgenticForwarder(port=0, runtime=Runtime.ASYNC, log_level=255)
await fwd.start_forwarder_async()
await fwd.stop_forwarder_async()
```

Async-only. Faces and classic CS/FIB/PIT via stock `AsyncMgmt`.

### 3. PiCN SimulationBus

**Cardiac network (paper use case):**

```bash
# Single narrative run
python -m demo.run_cardiac_bus --k 2 --seed 1

# Multi-run sweep with mean / median / stdev
python -m demo.run_cardiac_bus --seeds 1-5 --k-list 2,3,4 \
  --out demo/results/cardiac_network.jsonl --replace-out \
  --summary-json demo/results/cardiac_network_summary.json
```

Ambulance `submit_intent` → capability Interests on the bus → edge CS Content
→ Context~PIT aggregation → ranked hospital id. Summary JSON reports
**mean ± stdev** (and median) overall and per `k`.

**NFN stack-overhead:**

```bash
python -m demo.run_paired_bus --seeds 1-3 --k 2,3 --baseline nfn_async \
  --out demo/results/nfn_stack_overhead.jsonl --allow-dirty
```

## Cardiac structural campaign

Default grid ≈ **50 runs**: seeds `1–5` × `k ∈ {2,3,4,5,8}` × `{happy,adversary}`.

```bash
python -m demo.run_sweep --seeds 1-5 --k 2,3,4,5,8 --path happy,adversary \
  --out demo/results/cardiac_structural.jsonl --allow-dirty
```

JSONL metrics include latency, artefact bytes, Context PIT peak, dispatch
count, and `nfn_stack_overhead_publishable=false`.

## Figures

```bash
python -m demo.plot_metrics --in demo/results/cardiac_structural.jsonl \
  --out demo/results/figures/
```

### NFN stack-overhead gate

```bash
python -m demo.plot_metrics --in demo/results/nfn_stack_overhead.jsonl \
  --out demo/results/figures/ --include-m3
```

`--include-m3` **refuses** unless records have `m3_publishable=true`
(real plain-NFN baselines labelled `mode=plain_nfn`).

## Paper metrics map

| Id | Meaning | Notes |
|----|---------|-------|
| M1 | End-to-end latency | Absolute bus wall-clock is prototype-scale only |
| M2 | Message / artefact bytes | Structural sizes from scenario artefacts |
| M3 / nfn_stack_overhead | Agentic / plain-NFN ratio on combine | Only when publishable; **not** cardiac-over-bus |
| M4 | Adversary detection rate | Adversary path |
| M5 | Latency vs `k` | Scale axis |

## Reproducibility (A-011)

Harness sweeps require `--seed` / transport metadata and refuse dirty trees
unless `--allow-dirty`.

## Tests

```bash
python -m pytest agentic/tests/test_demo_cardiac_bus.py \
  agentic/tests/test_demo_bus_paired.py \
  agentic/tests/test_demo_sweep.py agentic/tests/test_demo_cli.py \
  agentic/tests/test_m3_publishable.py -v --timeout=90
```

## Related docs

* [Architecture](agentic.md) · [Hello agent](hello_agent.md) ·
  [Config / harness](agentic_config.md) · [Simulation](simulation.md)
* Package entry: [`demo/README.md`](../demo/README.md)
