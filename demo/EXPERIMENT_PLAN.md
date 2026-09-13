# Implementation Plan — Remaining Experiments for Agentic Routing

_Status: DRAFT (not yet implemented). Prepared 2026-09-08. This is the plan we resume from._

**Target venues:** full-conference → journal (IFIP Networking / GLOBECOM-ICC → IEEE TNSM / Computer Networks), reframed around **accountability and robustness without a centralized trust point**.

**Design principle:** reuse the existing measurement and framing discipline already in
`demo/report/commag_evaluation.md` (SimulationBus is illustrative, never a deployment number;
`time.perf_counter` around the scenario; `CardiacScenario` return dict → `MetricEvent`s; A-011 gates;
`transport=bus`). Everything below **extends** that, does not reinvent it.

---

## Experiment A — Centralized-Orchestrator Baseline Comparison

**Purpose:** Answer R1 #6/#7 ("what does this add beyond Temporal / Step Functions / SPIFFE?").
Show what you *get* with a conventional centralized orchestrator + tracing stack vs. the agentic
substrate, on the **same** cardiac intent.

**What exists:** the agentic side is fully built (cardiac structural + `run_cardiac_bus`).
**Missing: any orchestrator baseline.**

**Design — a `CentralizedOrchestrator` (new `agentic/baseline/orchestrator.py`):**
- A simple *in-process, synchronous* coordinator. Given the same parent intent, it (1) decomposes to
  the same leaf set (reuse `BoundedDecomposer`/template), (2) calls the same `DeterministicBackend`
  leaves, (3) collects results into a dict, (4) applies the same aggregation + ranking rule as the
  agentic case.
- Model *durable-workflow* semantics so it is not a strawman: an explicit **step log** (analogue of a
  Temporal/Step-Functions event history) and **retry** on leaf failure — so the comparison is
  "conventional orchestrator with a step log" vs "agentic substrate," not "naive loop."
- Produces `(completed, outcome, step_log, wall_time)` — measurably comparable to the agentic run.

**Metrics (same shape as existing `MetricEvent`s, new fields):**

| Quantity | Source |
|---|---|
| end-to-end wall time to intent completion | `time.perf_counter` around the orchestrator / `submit_intent` |
| messages / hops (agentic) vs leaf calls + steps (baseline) | orchestrator counter / scenario counter |
| **accountability present?** | agentic: verifiable Merkle trace root present; baseline: `step_log` exists but is **not cryptographically bound / no attestation** — record a boolean + why |
| completion + ranking correctness | both must rank identically (sanity check) |

**CLI (new `demo/run_baseline_compare.py`), mirroring existing style:**
```bash
python -m demo.run_baseline_compare --seeds 1-5 --k 2,3,4 \
  --out demo/results/baseline_compare.jsonl --summary-json demo/results/baseline_compare_summary.json
```
Include `--mode {agentic,centralized}` for paired runs.

**Output:** a side-by-side table (time + accountability-present + correctness) and one figure
(time-to-complete, grouped by mode). **First numbers:** agentic and baseline both complete the intent
in comparable wall-time at prototype scale, and the agentic run is the only one with a cryptographically
verifiable trace bound to the sub-intents.

---

## Experiment B — Single Point of Failure / Fault Tolerance

**Purpose:** Prove the thesis. Converts "we're neutral on latency" into "we keep accountability intact
when there is no central orchestrator to trust."

**What exists:** nothing. **Missing: failure injection + a comparison of outcomes.**

**Design:**
- Run the *same* intent under both modes with a **fault injected at a decision point** (the
  orchestrator/agent "dies" after delegating to the hospital leaves but before aggregation).
- `CentralizedOrchestrator` with a `fail_at` knob: raise/stop mid-step → the intent **cannot complete**
  (its state lives in the single process) and there is **no recoverable accountable trace** — the step
  log dies with the process.
- Agentic substrate: sub-intents are already in the network (Context-PIT, `C-FIB`); kill the *intake*
  agent and show the **leaves still get routed, results still aggregate into the Context-PIT trace
  root, and the trace stays verifiable** — because there is no single coordinator to lose.
- Inject the **same** failure (crash at the same logical point) in both, for a fair comparison.

**Key metrics:** (1) does the intent complete? (2) is the final trace verifiable (Merkle root valid
against the committed sub-intent set)? (3) what is recoverable / what is the blast radius? Record as
`{mode, completed, trace_verifiable, recovery_notes}`.

**CLI (`demo/run_spof.py`):**
```bash
python -m demo.run_spof --seeds 1-5 --k 3 --fail-at aggregate \
  --out demo/results/spof.jsonl
```
`--fail-at` in `{pre_delegate, post_delegate, aggregate}` and `--mode {agentic,centralized}`.

**Output:** outcome matrix (mode × failure point → completed / trace-verifiable), and a timeline
figure (which steps survive the fault). **First numbers:** centralized fails to complete and loses
verifiability at every failure point; agentic completes and keeps a valid trace for `post_delegate`
failures.

---

## Experiment C — Accountability-Cost vs. k

**Purpose:** Quantify what the trust substrate costs, so readers see it is not free but is *bounded and
modest*. Turns a "no overhead" claim into "here's the real overhead, and here's why it's worth it."

**What exists:** artefact-bytes-vs-k (already measured), attestation + Merkle trace (built), but
**no attribution of cost to the individual accountability mechanisms.**

**Design:**
- Add a **feature toggle** to the scenario so each mechanism can be enabled/disabled independently:
  `attestation`, `merkle_trace`, `reputation_update`.
- Run the same intent at each `k` (2,3,4,5,8) under: (a) all **off** (baseline mechanics only),
  (b) Merkle only, (c) Merkle + attestation, (d) all **on**.
- Measure **incremental** per-leaf and per-increment overhead (µs) plus the absolute cost of each
  component, and **artefact bytes** (already measured) as a vs-k curve.

**Metrics (added to `MetricEvent`):** `feature_flags`, `attestation_us`, `merkle_us`, `reputation_us`,
plus the existing `artefact_bytes` / `pit_peak` / `dispatch`.

**CLI (`demo/run_accountability_cost.py`):**
```bash
python -m demo.run_accountability_cost --seeds 1-5 --k 2,3,4,5,8 \
  --features merkle,attestation,reputation --out demo/results/accountability_cost.jsonl
```

**Output:** cost-vs-k curves (one line per feature stack) + a per-component breakdown table.
**First numbers:** Merkle/attestation cost grows ~linearly with `k` (it is per-leaf), reputation
update is near-constant in `k` (it is per-claim), total accountability overhead is a few
µs–ms at prototype scale — small relative to end-to-end intent time.

---

## Cross-cutting: harness & framing integration

- **Extend the `MetricEvent` schema** to add `mode`, `failed`, `trace_verifiable`, `feature_flags`,
  `attestation_us`, `merkle_us`, `reputation_us`, `baseline_present`. Backward-compatible (optional fields).
- **A-011 / reproducibility metadata:** record the exact experiment config (mode, feature flags, seed,
  k, version) in each record — the same discipline already used.
- **Publishability gates:** introduce `baseline_publishable` / `spof_publishable` /
  `accountability_publishable` flags analogous to the existing `m3_publishable`, so figures refuse to
  render unless the run is honest and complete.
- **Framing discipline (mandatory, per the ComMag reviews):** all output language stays *illustrative*;
  never "we beat X," always "the walkthrough demonstrates / the analysis suggests."

## File plan (create / modify)

| File | Action |
|---|---|
| `agentic/baseline/__init__.py`, `agentic/baseline/orchestrator.py` | **new** — `CentralizedOrchestrator` (durable step-log + retry + `fail_at`) |
| `demo/run_baseline_compare.py` | **new** — Experiment A runner |
| `demo/run_spof.py` | **new** — Experiment B runner (fault injection) |
| `demo/run_accountability_cost.py` | **new** — Experiment C runner (feature toggles) |
| `agentic/benchmark/...` (harness) | **modify** — extend `MetricEvent`; add publishability flags |
| `agentic/scenario/...` (CardiacScenario) | **modify** — accept `feature_flags` and `fail_at`; expose per-component timing |
| `agentic/tests/test_baseline_compare.py`, `test_spof.py`, `test_accountability_cost.py` | **new** — pytest parity with existing demo tests |
| `demo/generate_commag_report.py` (or new `generate_eval_report.py`) | **modify/extend** — emit the new tables + figures |

## How to run (localhost)

```bash
cd /Users/dgre/ai-agency/gh-repositories/PiCN
source .venv/bin/activate
python -m demo.run_baseline_compare --seeds 1-5 --k 2,3,4
python -m demo.run_spof --seeds 1-5 --k 3 --fail-at aggregate
python -m demo.run_accountability_cost --seeds 1-5 --k 2,3,4,5,8
pytest agentic/tests/test_baseline_compare.py agentic/tests/test_spof.py \
  agentic/tests/test_accountability_cost.py -v --timeout=90
```

---

## Review (ARCO) — risks & gaps to close

1. **Strawman risk (A):** a naive in-process loop makes the baseline look unfairly bad. Model
   durable-step + retry semantics so the comparison is honest.
2. **Apples-to-apples (A/B):** both modes must use the same leaves, same `DeterministicBackend`, same
   `k`, same aggregation + ranking rule.
3. **B must be fair:** the failure must be the *same* logical failure at the *same* logical point in
   both modes, not a contrived one. Document exactly what "fails."
4. **C attribution:** ensure the toggles are truly isolated (e.g. Merkle-on/attestation-off really skips
   attestation, not just the field), or the cost attribution is wrong.
5. **Schema change:** extending `MetricEvent` is the only change to shared code — do it
   backward-compatibly and re-run the existing `test_demo_*` suite to confirm no regressions.
6. **Caveat honesty:** keep the SimulationBus-illustrative framing in every caption; these are
   feasibility numbers, not deployment numbers.

---

## Open questions before implementation

1. **Baseline strength (A):** minimal in-process orchestrator with durable-step semantics
   (recommended) vs. a real Temporal/Step-Functions adapter (heavier, "harder" comparison).
   Recommendation: **minimal now, harden later.**
2. Should the subagent pipeline (REA/DESIREE/ARCO) be retried, or continue with direct drafting once
   the tool environment is stable?