# ComMag demo package

Operator-facing walkthroughs, measurement campaigns, SimulationBus demos,
and figure generation for the agentic cardiac-response prototype.

Library code stays under [`agentic/`](../agentic/). Full tutorial:
[`docs/agentic_demo.md`](../docs/agentic_demo.md). Minimal plug-in API:
[`docs/hello_agent.md`](../docs/hello_agent.md).

**No live LLM is required.** Hospital agents use `DeterministicBackend`.

## What this demo shows

1. **Cardiac structural (in-process)** — Context PIT commit-before-forward,
   Merkle trace root, adversary + KP-A reputation (deprioritise, not ban).
2. **Cardiac network demo (SimulationBus)** — ambulance
   `AgenticLayer.submit_intent` emits `/cap/fwd/...` capability Interests;
   edge `AgenticForwarder` answers with hospital capacity Content; ambulance
   aggregates and ranks (optional paediatric filter). **Not** NFN λ-names.
3. **NFN stack-overhead (SimulationBus)** — same NFN combine interest under
   `NFNForwarder` sync/async vs `AgenticForwarder` async (AgenticLayer idle
   for that name). Legacy metric key: `m3_*` / alias `nfn_stack_overhead_*`.

## Layout

```
demo/
  README.md                 # this file
  cardiac_walkthrough.py    # narrative happy + adversary (in-process)
  run_sweep.py              # cardiac structural multi-run → JSONL
  plot_metrics.py           # figures (stack-overhead gated)
  bus_topology.py           # NFN combine SimulationBus topologies
  cardiac_bus_topology.py   # paper-aligned /cap Interest bus topology
  run_cardiac_bus.py        # cardiac network demo CLI
  run_paired_bus.py         # NFN stack-overhead three-way comparison
  results/                  # gitignored local outputs
```

## Prerequisites

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# optional PNG figures:
pip install matplotlib
```

## Quickstart

```bash
# In-process cardiac story (structural)
python -m demo.cardiac_walkthrough --k 3 --seed 42

# Paper-aligned network story (capability Interests on SimulationBus)
python -m demo.run_cardiac_bus --k 2 --seed 1
```

## Create an agentic workflow

Three substrate options — pick one.

### A. Mock port (unit / offline)

Register a capability on `AgenticLayer` + `MockSubstratePort` (no PiCN faces).
See the full snippet in [`docs/hello_agent.md`](../docs/hello_agent.md).

### B. Live PiCN UDP (`AgenticForwarder`)

```python
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

fwd = AgenticForwarder(port=0, runtime=Runtime.ASYNC, log_level=255)
# fwd.register_capability(descriptor, backend)
await fwd.start_forwarder_async()
await fwd.stop_forwarder_async()
```

`AgenticForwarder` is **async-only**. Sync raises `SyncRuntimeNotSupported`.

### C. PiCN SimulationBus

**Cardiac network (paper use case):**

```bash
python -m demo.run_cardiac_bus --k 2 --seed 1

# Multi-run with mean / median / stdev:
python -m demo.run_cardiac_bus --seeds 1-5 --k-list 2,3,4 \
  --out demo/results/cardiac_network.jsonl --replace-out \
  --summary-json demo/results/cardiac_network_summary.json
```

**NFN stack-overhead microbenchmark:**

```bash
python -m demo.run_paired_bus --seeds 1-3 --k 2,3 --baseline nfn_async \
  --out demo/results/nfn_stack_overhead.jsonl --allow-dirty
```

## Cardiac structural sweep + figures

Default grid ≈ **50 runs** (`seeds 1-5` × `k ∈ {2,3,4,5,8}` × happy/adversary):

```bash
python -m demo.run_sweep --seeds 1-5 --k 2,3,4,5,8 --path happy,adversary \
  --out demo/results/cardiac_structural.jsonl --allow-dirty

python -m demo.plot_metrics --in demo/results/cardiac_structural.jsonl \
  --out demo/results/figures/
```

Structural records have `m3_publishable=false` / `nfn_stack_overhead_publishable=false`.

## NFN stack-overhead campaign

| Strategy | Primary node | Runtime |
|----------|--------------|---------|
| `nfn_sync` | `NFNForwarder` ×2 + `Fetch` | sync |
| `nfn_async` | `NFNForwarder` ×2 + `Fetch` | async |
| `agentic_async` | `AgenticForwarder` + companion NFN + `Fetch` | async only |

```bash
python -m demo.run_paired_bus --seeds 1-10 --k 3 \
  --out demo/results/nfn_stack_overhead.jsonl --allow-dirty

python -m demo.plot_metrics --in demo/results/nfn_stack_overhead.jsonl \
  --out demo/results/figures/ --include-m3
```

`--include-m3` **refuses** unless JSONL has `m3_publishable=true`
(alias: `nfn_stack_overhead_publishable`).

## Command cheat sheet

| Command | Purpose |
|---------|---------|
| `python -m demo.cardiac_walkthrough` | In-process cardiac happy + adversary |
| `python -m demo.run_cardiac_bus` | Paper: `/cap` Interests on SimulationBus |
| `python -m demo.run_sweep` | Structural multi-run → JSONL |
| `python -m demo.plot_metrics` | Figures from JSONL (stack-overhead gated) |
| `python -m demo.run_paired_bus` | NFN stack-overhead: sync + async + Agentic |

## Tests

```bash
python -m pytest agentic/tests/test_demo_cardiac_bus.py \
  agentic/tests/test_demo_bus_paired.py \
  agentic/tests/test_demo_sweep.py agentic/tests/test_demo_cli.py \
  agentic/tests/test_m3_publishable.py -v --timeout=90
```

## ComMag evaluation report

```bash
pip install matplotlib
python -m demo.generate_commag_report
# or reuse JSONL:
python -m demo.generate_commag_report --skip-runs
```

Outputs: [`demo/report/commag_evaluation.md`](report/commag_evaluation.md)
and PNG figures under `demo/report/figures/`.
