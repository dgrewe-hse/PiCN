# ComMag demo package

Operator-facing walkthroughs, measurement sweeps, SimulationBus comparisons,
and figure generation for the agentic cardiac-response prototype.

Library code stays under [`agentic/`](../agentic/). Full tutorial:
[`docs/agentic_demo.md`](../docs/agentic_demo.md). Minimal plug-in API:
[`docs/hello_agent.md`](../docs/hello_agent.md).

**No live LLM is required.** Hospital agents use `DeterministicBackend`.

## What this demo shows

1. **Cardiac scenario (in-process)** — Context PIT commit-before-forward,
   Merkle trace root, adversary + KP-A reputation (deprioritise, not ban).
2. **Measurement campaigns** — multi-run sweeps → JSONL → CSV/PNG figures.
3. **SimulationBus comparison** — same NFN combine interest under:
   - `NFNForwarder` **sync**
   - `NFNForwarder` **async**
   - `AgenticForwarder` **async** (only supported runtime)

Publishable **M3** (Agentic / plain NFN) appears only after paired bus runs.

## Layout

```
demo/
  README.md                 # this file
  cardiac_walkthrough.py    # narrative happy + adversary
  run_sweep.py              # Phase 1 multi-run → JSONL
  plot_metrics.py           # figures (M3 gated)
  bus_topology.py           # SimulationBus topologies (PiCN imports OK)
  run_paired_bus.py         # three-way NFN sync/async + Agentic async
  results/                  # gitignored local outputs
```

## Prerequisites

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# optional PNG figures:
pip install matplotlib
```

## Quickstart (cardiac walkthrough)

```bash
python -m demo.cardiac_walkthrough --k 3 --seed 42
```

Expect verified trace roots, ranking, intake, and adversary KP-A lines.

## Create an agentic workflow

Three substrate options — pick one.

### A. Mock port (unit / offline)

Register a capability on `AgenticLayer` + `MockSubstratePort` (no PiCN faces).
See the full snippet in [`docs/hello_agent.md`](../docs/hello_agent.md).

```bash
# After copying the hello_agent example into a file:
python your_hello_agent.py
```

### B. Live PiCN UDP (`AgenticForwarder`)

```python
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

fwd = AgenticForwarder(port=0, runtime=Runtime.ASYNC, log_level=255)
# fwd.register_capability(descriptor, backend)
await fwd.start_forwarder_async()
# configure faces / FIB via AsyncMgmt (classic ICN tables only)
await fwd.stop_forwarder_async()
```

`AgenticForwarder` is **async-only**. Sync raises `SyncRuntimeNotSupported`.

### C. PiCN SimulationBus (multi-node, this package)

Same NFN combine interest, three strategies (see Phase 2 below):

```bash
python -m demo.run_paired_bus --seeds 1-3 --k 2,3 --baseline nfn_async \
  --out demo/results/phase2_paired.jsonl --allow-dirty
```

Classic PiCN NFN-only tutorials (no agentic layer):
`PiCN/Simulations/SimulationsTutorial.py` (sync) and
`PiCN/Simulations/SimulationsTutorial_async.py` (async). Background:
[`docs/simulation.md`](../docs/simulation.md).

## Phase 1 — measurement sweep + figures

Default grid ≈ **50 runs** (`seeds 1-5` × `k ∈ {2,3,4,5,8}` × happy/adversary):

```bash
python -m demo.run_sweep --seeds 1-5 --k 2,3,4,5,8 --path happy,adversary \
  --out demo/results/phase1.jsonl --allow-dirty

python -m demo.plot_metrics --in demo/results/phase1.jsonl \
  --out demo/results/figures/
```

Phase 1 records have `m3_publishable=false` (no synthetic M3).

## Phase 2 — SimulationBus strategies

| Strategy | Primary node | Runtime |
|----------|--------------|---------|
| `nfn_sync` | `NFNForwarder` ×2 + `Fetch` | sync |
| `nfn_async` | `NFNForwarder` ×2 + `Fetch` | async |
| `agentic_async` | `AgenticForwarder` + companion NFN + `Fetch` | async only |

```bash
python -m demo.run_paired_bus --seeds 1-10 --k 3 \
  --out demo/results/phase2_paired.jsonl --allow-dirty

# M3 denominator (default async NFN — peer of Agentic):
python -m demo.run_paired_bus --baseline nfn_async ...
python -m demo.run_paired_bus --baseline nfn_sync ...

# Unlock M3 figures:
python -m demo.plot_metrics --in demo/results/phase2_paired.jsonl \
  --out demo/results/figures/ --include-m3
```

`--include-m3` **refuses** unless JSONL has `m3_publishable=true`.

JSONL highlights: `bus.nfn_sync_elapsed_ms`, `nfn_async_elapsed_ms`,
`agentic_async_elapsed_ms`, `metrics.m3_vs_nfn_sync`, `m3_vs_nfn_async`.

## Command cheat sheet

| Command | Purpose |
|---------|---------|
| `python -m demo.cardiac_walkthrough` | In-process cardiac happy + adversary |
| `python -m demo.run_sweep` | Multi-run campaign → JSONL |
| `python -m demo.plot_metrics` | Figures from JSONL (M3 gated) |
| `python -m demo.run_paired_bus` | Bus: NFN sync + async + Agentic async |

## Tests

```bash
python -m pytest agentic/tests/test_demo_bus_paired.py \
  agentic/tests/test_demo_sweep.py agentic/tests/test_demo_cli.py \
  agentic/tests/test_m3_publishable.py -v --timeout=90
```

## Publishing

Work lands on `agentic/commag-demo-measurements`, then merges into
`agentic/implementation`. Optionally cut `agentic/demo` later for a stable
ComMag artifact pointer.
