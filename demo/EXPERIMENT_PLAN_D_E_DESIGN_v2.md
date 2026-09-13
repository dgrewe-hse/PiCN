<task id="ses_f6ab3c170ffec2peZ7WvfCniL7" state="completed">
<task_result>
I have all context. Key verified facts for the wire change: `Interest` carries only `name` (no parameters/parameters field), `Content` carries `name` + `content`; the current `_match_outstanding` uses bidirectional `is_prefix_of`; `_outstanding` maps `correlation -> Name`; `send_request` explicitly does `_ = payload`.

Now I'll write the complete DESIGN v2.

---

# DESIGN v2 — Experiments D & E: Concurrent Fan-Out, T_intent Decomposition, and the Physical Testbed for Agentic Routing

**Author:** DESIREE 🏗️ (architecture & design)
**Status:** DESIGN v2 (supersedes v1; not implemented)
**Repo:** `gh-repositories/PiCN` (fork of Basel PiCN)
**Target:** revise COMMAG-26-00578 (rejected) + TNSM/Computer Networks reframing
**Date:** 2026-09-12
**Incorporates:** Decisions 1–8 (Dennis, 2026-09-12, appended to v1 design)

---

## 0. Verification notes (what I read, what I could NOT verify)

**Verified by reading (v1 findings retained; re-confirmed):**
- `submit_intent` leaf loop is serial: `agentic/agentic_layer/layer.py:296-311` — plain `for … await self._port.send_request(...)`. No `asyncio.gather`, no `TaskGroup`.
- I2 holds structurally: `self._pit.commit(...)` at `layer.py:284-290` runs **before** the forward loop at `layer.py:296`. Commit is synchronous (no `await`), so concurrency added after it cannot reorder commit relative to send.
- Leaf→index mapping is keyed by `correlation` in `self._leaf_index` (`layer.py:300`); response recording is `self._pit.record_response(parent, leaf_index, digest)` (`layer.py:213-220`). **Index-keyed, not completion-order-keyed** → out-of-order completion is safe. `context_pit.py` addresses leaves by index (`entry.tree.set_leaf(leaf_index, …)`).
- `DeterministicBackend.invoke` (`binding/deterministic.py:40-46`) awaits awaitables; current cardiac handler (`scenario.py:222-226`) is synchronous → zero latency.
- Cardiac `_run_exchange` (`scenario.py:346-482`) awaits each hospital serially at `scenario.py:400`; **it bypasses `AgenticLayer.submit_intent` and drives `ContextPIT` directly.** D targets `submit_intent` (see §2.0).
- `MetricEvent` is `(kind, transport, seed, value, labels)`; `EventKind` is a closed `Literal` (`benchmark/events.py:14-26`); `labels` is `dict[str, Any] | None` → new dimensions ride in `labels` without touching `MetricEvent`.
- Publishability gate pattern: `_baseline_is_publishable` (`metrics.py:19-28`) → `m3_publishable` (`metrics.py:54`); alias `nfn_stack_overhead_publishable` (`metrics.py:62-65`).
- AC1 forbids `agentic.benchmark` importing `PiCN.*`. **Constraint for E: the physical runner lives under `demo/`, not `agentic/benchmark/`.**
- AC3 forbids LLM clients in `agentic.port`, `agentic.adapters`, `agentic.agentic_layer`, `agentic.trust`; `pydantic_ai` lives only in `binding/pydantic_ai_backend.py`.
- `PicnSubstratePort` already supports real UDP: `UDP4Interface(0)` default and `AddressInfo((peer_host, peer_port), 0)` when `peer_port is not None` (`adapters/picn/port.py:99-107`). E's UDP path is implemented, untested on hardware.
- `PicnSubstratePort._match_outstanding` (`port.py:262-269`) matches **bidirectionally** (`interest_name == content_name or interest_name.is_prefix_of(content_name)` **or** `content_name.is_prefix_of(interest_name)`), and `_outstanding` maps `correlation -> Name` (`port.py:110, 187`). `send_request` does `_ = payload` (`port.py:210`) — **Interest parameters are not currently transmitted** (confirmed for A-012's wire-change framing).
- `MockSubstratePort` supports deterministic out-of-order release via `hold_terminals=True` + `release_terminals(order)` (`adapters/mock/port.py:74, 161, 270`; exercised in `test_aggregation.py:170-187`).
- `PydanticAIBackend` reads `[model] preference` via `load_model_preference` (`pydantic_ai_backend.py:32-55`); local-first, `["test"]` default when path is `None`.
- **Wire reality for A-012 (new):** `PiCN/Packets/Interest.py:10-16` — `Interest(name, wire_format)` only; there is **no parameters field**. `Content(name, content, wire_format)` (`Content.py:10-18`) carries name + content only. A correlation token cannot ride a current Interest field; A-012 must add one to the **agentic internal representation** and to the PiCN packet path (see A-012 §"Wire/parameter change" — flagged as the largest implementation surface).

**Could NOT verify (flagged, no guessing):**
- **No `LatencyBackend` exists anywhere.** D/E must introduce it.
- **No Raspberry Pi / Ansible / inventory artifacts exist in-repo.** E's provisioning structure is greenfield.
- **`runs-per-cell` / statistical policy for UDP does not exist.** Only trimming logic is `_trim_cold_start` (`demo/generate_commag_report.py:42-45`, cap 500 ms), bus-only. No UDP policy.
- **No `LatencyObserver` exists.** New in v2.
- **Whether PiCN's packet encoder can carry an Interest parameter token without a wire-format change** — not verified; `SimpleStringEncoder`/`BasicEncoder` internals were not read. This is flagged in A-012 as CRAFT's first investigation and the ADR's primary risk.
- `MAX_CONTEXT_PIT_ENTRIES=256` and `DEFAULT_INBOUND_SIZE=64` are marked PROVISIONAL; D peak-in-flight and E node count interact with them (flagged §7).
- Whether `PicnSubstratePort.send_request` is safe under many concurrent in-flight Interests **after** Decision 1's fix — design claims safety, must be proven by test (A-012 verification).

---

## 1. Goal & reviewer-mapping (v2)

### 1.1 The two skeptical-committee objections this must kill

> **(a)** "The strongest ICN win is untested. Everything is a single in-flight intent on a shared bus. The actual benefit of name/fan-out in an ICN/agentic overlay is untested."
>
> **(b)** "What does your solution add? Temporal / Step Functions / SPIFFE / OTel / MCP already orchestrate fan-out and time it."

D and E jointly attack (a). The **T_intent decomposition** is the v2 instrument that attacks (b): it does not merely report "we are fast"; it reports **what share of an intent's end-to-end time each substrate stage contributes**, so the reviewer can see exactly what the agentic/ICN layer adds and whether that share is bounded. A single wall-clock number cannot answer (b); a decomposition can.

### 1.2 Reviewer complaint → experiment mapping (v2)

| Complaint | Source | Answered by | How |
|---|---|---|---|
| R1 #6 / #7 — weak comparison vs Temporal / Step Functions / SPIFFE / OTel / MCP; **"what does Agentic Routing add?"** | R1 | **D + E via T_intent decomposition** | `T_intent = T_decompose + T_dispatch + T_network + T_service + T_aggregate` isolates the substrate's contribution (dispatch + network + aggregate) from service time. The reviewer's question is answered numerically, not rhetorically: "at edge-realistic service latency, the substrate contributes X ms / Y% of T_intent." A/B/C compare *what you get*; D/E measure *the substrate's share of the cost*. |
| **Committee concurrency objection** — single in-flight intent, fan-out benefit untested | committee | **D (primary), E (secondary)** | D produces a **speedup-vs-k curve** on the exact leaf set with a paired serial baseline, PLUS the T_intent decomposition showing dispatch overlap vs serialization. E repeats the decomposition over real UDP. |
| R1 #3 / #4 — Context PIT omission-accountability / attestation limits underspecified | R1 | **D (partial), E (partial)** | D stress-tests omission accounting under out-of-order completion at k up to 8; E across real nodes. Deeper attestation work remains C's remit. |
| R3 #3 / R4 — no implementation / simulation / PoC | R3, R4 | **D AND E** | Two PoCs at different fidelity tiers (Decision 8). |
| R4 — emergency scenario too high-level | R4 | **E** | Physical multi-node deployment with named roles; capability-name routing across ≥2 physical edges (Decision 4). |
| "Your speedup is a measurement artifact" | anticipated | **D zero-latency control cell (Decision 7)** | `leaf_latency_s=0` cells must yield ~1.0× — proving the speedup tracks real service overlap, not the harness. |

### 1.3 Non-goals (explicit)

- D does **not** claim real network behaviour. Its `T_network` is near-zero by construction; D's honest claim is **dispatch/aggregation/decomposition isolation** (Decision 3 honesty trap).
- E does **not** claim a controlled comparison against D. It is a physical deployment measurement.
- Neither experiment touches the NFN combine microbenchmark (`m3` / `nfn_stack_overhead`).
- The **full wildcard-resolver table is future work** (Decision 1); A-012 fixes the demux bug and makes the token+LPM fallback correct under wildcards, but does not implement a wildcard resolver.

---

## 2. Experiment D v2 — Concurrent fan-out ＋ T_intent decomposition (SimulationBus, DeterministicBackend)

### 2.0 Critical scope clarification (unchanged from v1, re-confirmed)

Two fan-out paths exist; they are different code:

1. **`AgenticLayer.submit_intent`** (`layer.py:296-311`) — the real forwarding path over a `SubstratePort`. **D's primary target.**
2. **`CardiacScenario._run_exchange`** (`scenario.py:396-408`) — the structural in-process path driving `ContextPIT` directly, bypassing `submit_intent` and the port.

**Decision (unchanged):** D targets the **AgenticLayer path** because that is the ICN forwarding path under test and it exercises the same `SubstratePort` E uses. `CardiacScenario` is not modified for concurrency in D. A concurrent structural variant remains a separate future experiment.

### 2.1 Concurrency mechanism + exact insertion points (re-confirmed after Decision 3 observer changes)

**Mechanism: `asyncio.TaskGroup`.** Structured cancellation; `send_request` is documented not to raise across the port boundary (`port/protocol.py:11`), so `ExceptionGroup` risk is low. D unwraps an `ExceptionGroup` to the first leaf and re-raises (ADR-007 unwrap discipline).

**Insertion point — `submit_intent` (verified `layer.py:295-313`):**

```python
# Forward only after commit (I2).
for leaf_index, leaf in enumerate(entry.leaves):
    correlation = hashlib.sha256(
        parent_intent_digest + leaf.sub_intent_digest
    ).digest()
    self._leaf_index[correlation] = (parent_intent_digest, leaf_index)
    self._pit.mark_forwarded(parent_intent_digest, leaf_index)
    name = self._port.name_from_components(
        (b"cap", b"fwd") + tuple(c.encode("utf-8") for c in leaf.capability)
    )
    await self._port.send_request(
        name, leaf.payload, correlation=correlation, deadline=deadline,
    )
return await done
```

**Design (description only, no code):**
- Add `dispatch: DispatchMode = "serial"` to `submit_intent`; default preserves existing behaviour bit-for-bit.
- The **commit** (`layer.py:284-290`) and the `done` future (`layer.py:291-293`) remain **untouched and before** the loop. I2 preserved: commit is synchronous, precedes both paths.
- **Pre-dispatch synchronous prepass** (both modes): for every leaf precompute `correlation`, register `self._leaf_index[correlation] = (parent, leaf_index)`, call `self._pit.mark_forwarded(parent, leaf_index)`, and build the wire `name`. Rationale unchanged: a fast response must never arrive before the mapping exists (`_on_response` drops on `mapping is None` → leaf stuck PENDING → NULL at terminate).
- **Serial path:** unchanged loop over prepass tuples.
- **Concurrent path:** one `async with asyncio.TaskGroup() as tg:` block, one `tg.create_task(self._port.send_request(...))` per leaf. All sends scheduled before any is awaited.

**Decision 3 impact on the mechanism — confirmed still holds:**
- The `LatencyObserver` is **not** inside `submit_intent`'s control flow; it is injected and observed at the port-event boundary (§2.2). It does not introduce an `await` between commit and first dispatch, and it does not reorder leaves.
- The prepass now also emits observer **`t_dispatch_start`** / **`t_dispatch_end`** markers around the dispatch block (via the observer, not inline logic), preserving the "no measurement in the hot core" rule.
- **Invariant preservation argument (unchanged):** I2; index-addressed Merkle (`_on_response`/`_on_timeout`/`_on_failed` look up by correlation, `layer.py:213-237`); `maybe_complete` idempotent (`context_pit.py:225-226`); `done` guarded by `if not fut.done()` (`layer.py:244`); all mutations synchronous between `await`s. **To be proven by D's out-of-order test, not assumed.**

### 2.2 NEW — `LatencyObserver` design (Decision 3)

**Purpose:** produce the per-leaf timestamps that decompose `T_intent` into five components, reused identically by D (SimulationBus) and E (UDP) so the decomposition is comparable across fidelity tiers. It **extends** v1's `DispatchObserver`.

**Placement / injection (must not leak into the hot layer core):**
- Lives in `agentic/agentic_layer/observer.py` (new; imports only stdlib + `agentic.port.events` — **no `PiCN.*`**, AC1/AC2 safe; no LLM, AC3 safe).
- Injected into `AgenticLayer` as a constructor kwarg `observer: LatencyObserver | None = None` (default `None` → a `NullLatencyObserver` no-op). `layer.py` calls only observer hooks; it never computes derived metrics, never imports the observer implementation, and **never emits `MetricEvent`s** (that stays in `agentic/benchmark`).
- The D/E runners pass a concrete `RecordingLatencyObserver` that collects timestamps; the benchmark layer turns them into events.

**Interface (Protocol — design contract, exact method set):**

```python
class LatencyObserver(Protocol):
    def on_commit(self, *, parent: bytes, t_commit: float, leaf_count: int) -> None: ...
    def on_dispatch_begin(self, *, parent: bytes, t0: float) -> None: ...
    def on_leaf_forwarded(self, *, correlation: bytes, parent: bytes,
                          leaf_index: int, name: str, t_send: float) -> None: ...
    def on_leaf_response(self, *, correlation: bytes, parent: bytes,
                         leaf_index: int, t_response: float) -> None: ...
    def on_dispatch_end(self, *, parent: bytes, t1: float) -> None: ...
    def on_aggregate(self, *, parent: bytes, t_aggregate: float,
                     trace_root: bytes) -> None: ...
    def on_complete(self, *, parent: bytes, t_intent: float) -> None: ...
```

**Where each timestamp comes from (grounded):**

| Symbol | Source | Clock domain |
|---|---|---|
| `t0` (commit) | `time.perf_counter()` at the observer's `on_commit`, called from `submit_intent` immediately after `self._pit.commit(...)` returns (`layer.py:290`). | local monotonic |
| `t_send` | `RequestSent.at` event (port emits it: `adapters/picn/port.py:189`, `mock/port.py:264`) — captured in `_dispatch_event`'s `RequestSent` branch (`layer.py:176-177`, currently a no-op `return`). | port clock |
| `t_response` | `ResponseArrived.at` (port emits it: `port.py:287-294`) — captured in `_on_response` (`layer.py:213`). | port clock |
| `t_service` | **producer side**, measured around `self._hub.invoke(...)` in `_on_inbound_request` (`layer.py:198-200`). D (intake-only timestamping) records `t_service` **only when the producer is the intake itself** (co-located) — see honesty trap. | producer clock |
| `t1` (dispatch end) | `time.perf_counter()` at `on_dispatch_end`, called after the dispatch block returns (both modes). | local monotonic |
| `t_agg` (aggregate) | `time.perf_counter()` at `on_aggregate`, called from `_maybe_finish` right after `maybe_complete` returns a completed entry (`layer.py:240-245`). | local monotonic |
| `t_intent` | `time.perf_counter()` at `on_complete`, called when `done` resolves (`await done` returns, `layer.py:313`). | local monotonic |

**T_intent's 5 components — exact definitions (simulation-clock-adjusted, single clock per tier):**

```
T_decompose = t_commit - t_emission            # emission = application calls submit_intent
T_dispatch  = t1 - t0                           # dispatch block wall-time (serial: k·L; concurrent: ~L)
T_network   = Σ_leaf (t_response - t_send) / k  # per-leaf mean network/transport time
T_service   = Σ_leaf t_service / k              # per-leaf mean producer backend time
T_aggregate = t_intent - t1                     # wait-after-last-dispatch: aggregation + completion
```

> **Accounting rule (anti-double-count):** `T_dispatch` already *contains* the per-leaf `T_network` and `T_service` because it is wall-time over the dispatch block. Therefore **two reportable views** are defined and must never be summed into a single "T_intent":
> - **View A (wall):** `T_intent = T_decompose + T_dispatch + T_aggregate` (exact, no double counting).
> - **View B (attribution):** `T_network` and `T_service` are **decompositions *inside* `T_dispatch`**, reported as means and as share-of-`T_dispatch`. A composite attribution line `T_intent ≈ T_decompose + T_network + T_service + max(0, T_dispatch − T_network − T_service) + T_aggregate` is permitted **only** with the residual labelled `T_dispatch_residual` (scheduling + fan-out overhead). This is the honest framing and is enforced in the report generator.
>
> The v1 headline `fanout_speedup` (serial/concurrent `T_dispatch`) is **retained** as the concurrency-specific metric; `T_intent` decomposition is the substrate-share metric. Both are emitted.

**Honesty traps encoded:**
- **D (SimulationBus):** `T_send`/`T_response` come from the in-process bus; **`T_network` is near-zero by construction.** D's captions must say its claim is **dispatch/aggregate/decomposition isolation**, never transport latency. `T_network` may be reported only as "≈0, by construction, not a network measurement."
- **E (real UDP):** `T_network` is real and measurable — report its actual share.
- **Co-located vs distributed:** D uses **intake-side timestamps only** (T_service reported only for co-located producers, else omitted and marked `service_unmeasured`). E adds **producer-side timestamps** with measured clock-offset correction (§3.2).

**Observer overhead control (new risk, §7):** the observer must be measured or disabled. D provides `--observer {on,off}`; a calibration cell runs `observer=off` to bound observer cost, reported as `observer_overhead_ms`. The `NullLatencyObserver` is the default when `--observer off`.

### 2.3 Metric schema deltas v2 (T_intent decomposition + speedup)

**Backward-compatibility rule (unchanged):** `MetricEvent` keeps `(kind, transport, seed, value, labels)` exactly. No new required fields. All new dimensions ride in `labels`.

**New `EventKind` values** (append to the `Literal`, `events.py:14-26`; purely additive):

| New `EventKind` | `value` | `labels` | Meaning |
|---|---|---|---|
| `t_intent_ms` | `T_intent` ms | `{k, mode, leaf_latency_s, campaign}` | End-to-end intent wall-time (View A) |
| `t_decompose_ms` | `T_decompose` ms | same | Decomposition stage |
| `t_dispatch_ms` | `T_dispatch` ms | same | Dispatch-block wall-time |
| `t_network_ms` | mean `T_network` ms | same | Transport time (D≈0) |
| `t_service_ms` | mean `T_service` ms | same + `{measured: true\|false}` | Producer backend time |
| `t_aggregate_ms` | `T_aggregate` ms | same | Aggregation + completion |
| `t_dispatch_residual_ms` | residual ms | same | `T_dispatch − T_network − T_service` |
| `fanout_serial_ms` | wall ms | `{k, leaf_latency_s, mode:"serial", campaign}` | Paired serial dispatch wall-time |
| `fanout_concurrent_ms` | wall ms | `{k, leaf_latency_s, mode:"concurrent", campaign}` | Concurrent dispatch wall-time |
| `fanout_speedup` | `serial/concurrent` | `{k, leaf_latency_s}` | Speedup (paired runs only) |
| `leaf_inflight_peak` | count | `{k, mode}` | Peak simultaneous in-flight leaves |
| `leaf_overlap_fraction` | 0.0–1.0 | `{k, mode}` | Fraction of leaf-pairs whose intervals overlap |
| `leaf_start_ms` | offset ms | `{k, mode, leaf_index}` | Per-leaf start (debug/overlap) |
| `leaf_end_ms` | offset ms | `{k, mode, leaf_index}` | Per-leaf end (debug/overlap) |
| `observer_overhead_ms` | ms | `{mode, calibration:true}` | Observer cost (observer on vs off same cell) |
| `config_snapshot` | 0 | full config | One per campaign |

Existing kinds untouched. `Literal` widening is producer-backward-compatible.

**`MetricsSnapshot` additive optional fields** (`metrics.py:31-60`, `None` defaults):
`concurrent_publishable: bool|None`, `fanout_serial_ms`, `fanout_concurrent_ms`, `fanout_speedup`, `leaf_inflight_peak`, `leaf_overlap_fraction`, `t_intent_ms`, `t_decompose_ms`, `t_dispatch_ms`, `t_network_ms`, `t_service_ms`, `t_aggregate_ms`, `t_dispatch_residual_ms`, `observer_overhead_ms`, `physical_publishable: bool|None`.

Paper aliases (mirror `nfn_stack_overhead_*`): `concurrent_dispatch_publishable` → `concurrent_publishable`; `physical_deployment_publishable` → `physical_publishable`; `t_intent_total_ms` → `t_intent_ms`.

`compute_metrics` gains **no new hot-path computation**; a short additive block at the end reads the new events into optional fields, so outputs for existing event sets are byte-identical when the new kinds are absent. D aggregation lives in new `agentic/benchmark/concurrency.py`.

### 2.4 Sweep specification (two axes + zero-latency control)

**Sweep axes:**
- `--concurrency {serial,concurrent}` — default runs **both** (paired).
- `--leaf-latency-s {0,0.01,0.05,0.1,0.5}` — **Decision 3 controlled service-time axis.**
- `--k 2,3,4,5,8`.
- `--seeds 1-5` (configurable).
- `--path happy`; `--transport bus`.

**Control cell (Decision 7):** `leaf_latency_s = 0` is run in **every** campaign (contemporaneous, not optional). Expected `fanout_speedup ≈ 1.0×`. It is a **control, not a publishable speedup cell** (see gate §2.5).

**Paired-run semantics (unchanged):** for each `(seed, k, leaf_latency_s)` cell, run serial then concurrent on the **same leaf set**, producing one record with both times, both decompositions, and the derived speedup. A cell is valid only if both modes completed with identical `k` and byte-identical trace roots.

**CLI — `demo/run_concurrency.py`:**

```bash
python -m demo.run_concurrency \
  --seeds 1-5 \
  --k 2,3,4,5,8 \
  --concurrency serial,concurrent \
  --leaf-latency-s 0,0.01,0.05,0.1,0.5 \
  --observer {on,off} \
  --transport bus \
  --out demo/results/concurrency.jsonl \
  --summary-json demo/results/concurrency_summary.json \
  --allow-dirty
```

Output: one JSONL record per `(seed,k,leaf_latency_s)` with both modes' `T_intent` decomposition, speedup, peak in-flight, overlap, `trace_root_equal`, and run metadata. Summary emits: speedup-vs-k curve at each `leaf_latency_s`; serial/concurrent `T_dispatch`-vs-k; and the **T_intent composition stacked bars** per `leaf_latency_s`.

### 2.5 Publishability gate `concurrent_publishable` (v2)

> `concurrent_publishable` is `True` **iff all** hold for a cell:
> 1. Both `fanout_serial_ms` and `fanout_concurrent_ms` exist for the **same** `(transport="bus", seed, k, leaf_latency_s)`.
> 2. `labels["leaf_latency_s"] > 0` (zero-latency cells are **controls**: they may render the ~1.0× control plot but **never** a speedup claim; they are exempt from this condition *only* when a `labels["control"]=true` companion flag is present and the figure renders them separately).
> 3. Same leaf set: identical `k` and identical recorded `expected_subintent_set` digests.
> 4. `trace_root_equal is True` (accountability not traded for speed).
> 5. `fanout_speedup` finite and `> 0`.
> 6. All five T_intent components present with finite values, **and** `labels["T_intent_view"]` ∈ `{"A"}` (View A) or the record carries an explicit `T_dispatch_residual_ms` (View B).
>
> Failing any → `concurrent_publishable=False`; the figure layer refuses to render the speedup curve (same discipline as `_plot_bar_ratios` reading `m3_publishable`).

**Anti-inflation (unchanged):** speedup only from a genuinely paired same-leaf-set run. Dividing independent serial/concurrent means from different sweeps is forbidden and blocked by condition 1.

### 2.6 File plan (Experiment D v2)

| File | Action | Purpose |
|---|---|---|
| `agentic/binding/latency.py` | **new** | `LatencyBackend` wrapper (Decision 2) |
| `agentic/binding/__init__.py` | modify | export `LatencyBackend` |
| `agentic/agentic_layer/observer.py` | **new** | `LatencyObserver` Protocol + `NullLatencyObserver` + `RecordingLatencyObserver` |
| `agentic/agentic_layer/dispatch.py` | **new** | `DispatchMode` enum (v1's observer folds into `observer.py`) |
| `agentic/agentic_layer/layer.py` | modify | `dispatch` param; prepass; `TaskGroup` branch; observer hooks at commit/forward/response/aggregate/complete; producer `t_service` around `_hub.invoke` |
| `agentic/benchmark/events.py` | modify | append new `EventKind` values (additive) |
| `agentic/benchmark/concurrency.py` | **new** | D snapshot: decomposition, speedup, peak, overlap; `concurrent_publishable` gate |
| `agentic/benchmark/metrics.py` | modify | optional fields + aliases (additive block) |
| `agentic/benchmark/__init__.py` | modify | export new snapshot/gate |
| `agentic/adapters/picn/port.py` | modify | **A-012**: directional LPM + correlation token (see §5/ADR) |
| `demo/run_concurrency.py` | **new** | D runner CLI (paired × service-latency sweep + control cell) |
| `demo/concurrency_topology.py` | **new** | D wiring: ambulance `AgenticLayer` + edge producer with `LatencyBackend`, SimulationBus, observer |
| `demo/generate_concurrency_report.py` | **new** | speedup-vs-k + T_intent stacked-bar figures + honest SimulationBus captions |
| `agentic/tests/test_concurrency_paired.py` | **new** | serial vs concurrent same leaves → identical root, speedup>1 with latency, peak, overlap |
| `agentic/tests/test_latency_backend.py` | **new** | wrapper determinism, jitter seeding, schema delegation, conformance |
| `agentic/tests/test_latency_observer.py` | **new** | component definitions; single-clock; Null observer no-op |
| `agentic/tests/test_concurrent_publishable.py` | **new** | gate true only on paired same-leaf-set non-zero-latency runs |
| `agentic/tests/test_submit_intent_i2_concurrent.py` | **new** | out-of-order completion via `hold_terminals`+reversed release → correct root |
| `agentic/tests/test_name_correlation.py` | **new** | **A-012**: prefix-trap (`h1` vs `h10`) demux test |
| `demo/README.md`, `demo/EXPERIMENT_PLAN.md` | modify | document D, gate, control cell |

**Import discipline:** `layer.py` imports no `PiCN.*` beyond `PiCN.Processes`/`PiCN.Packets` (AC2); `observer.py` and `dispatch.py` import no PiCN → safe.

---

## 3. Experiment E v2 — Physical testbed (2+ edges, UDP, staged LLM)

### 3.1 Topology — `--edges N` (Decision 4)

**Roles (minimum 8 nodes, first publishable campaign N=2 edges):**

| # | Role | Node software | Interfaces |
|---|---|---|---|
| 1 | **Intake / Ambulance** | `AgenticLayer` + **one `PicnSubstratePort` per edge** + application driver + `RecordingLatencyObserver` | UDP toward each edge |
| 2–3 | **Edge / Hospital forwarders** (`--edges 2`) | `AgenticForwarder` with registered producers for its owned capability prefixes | UDP toward intake; optional peer face |
| 4–5 | **Capability producers** (fold into edges or dedicated) | `AgenticForwarder` producers (deterministic / latency / LLM) | UDP |
| 6 | **LLM host A** (on-Pi Ollama, E-LLM staged A) / **GPU host C** (DGX Spark on LAN, staged C) | `PydanticAIBackend` behind an edge forwarder (A) or as a producer reachable via an edge (C) | UDP |
| 7 | **Observer / measurement node** | Collects JSONL + per-node clock offsets; off the data path | mgmt |
| 8+ | **Spare / scale-out** | additional edges/producers | UDP |

**Capability-name routing across physical nodes (the Decision 4 payoff):**
- Intake holds **one `PicnSubstratePort` per edge**; hospital capability prefixes map to the owning edge. Example first campaign: hospital group H1..H4 served by edge-1 via prefixes `/cap/fwd/hospital/beds/h1*` … `/cap/fwd/hospital/beds/h4*`; H5..H8 by edge-2. k=8 split across the two edges.
- Routing decision at the intake is a **true directional LPM** over the registered prefix table (`longest_prefix_match`, `port.py:167-175`) — this is the mechanism the paper demonstrates, and it **interoperates with Decision 1** (token echoed in Content; LPM is the fallback for wildcard/placeholder names).
- N=1 (single edge, all k on it) remains a **supported control cell** for the topologies workbook.
- The intake fans out over multiple ports; each port uses `peer_host=<edge IP>, peer_port=<edge UDP port>` (`port.py:99-107`). **No adapter change needed for multi-edge basic UDP.**

**Distributed / aggregated Context PIT (unchanged, honest):**
- Context PIT is **in-memory, node-local**. E's claim: the **intake holds the authoritative Context PIT** for a parent intent and aggregates remote responses; the Merkle trace root is computed **at the intake**. Remote producers hold no parent PIT. State this verbatim.
- **Not claimed:** cross-node PIT replication, consensus, distributed Merkle root.
- Aggregation across nodes = multiple remote producers answer leaves of one parent held at the intake; out-of-order arrival handled by index keying.

### 3.2 LatencyObserver reuse over UDP + clock-offset correction (Decision 3)

- **Same observer, same interface** as D (§2.2). Reuse is mandatory so the decomposition is comparable across fidelity tiers.
- **Intake-side timestamps** (`t0`, `t1`, `t_agg`, `t_intent`, `t_send`, `t_response`): single monotonic clock at the intake. Safe, primary.
- **Producer-side `t_service`:** each edge/producer runs the observer locally around `_hub.invoke` and writes a per-leaf **service record** (`{run_id, parent, leaf_index, correlation, t_service_ns, clock_offset_ns}`) to its results dir; the observer node collects them.
- **Clock-offset correction:** before each campaign, run an NTP/chrony-offset measurement (ping-pong or chrony `tracking` parse) between intake and each producer; record offset and residual uncertainty. Producer `t_service` is corrected to the intake clock domain. If residual skew > threshold (default 1 ms, configurable), the run is **excluded** from the publishable set and reported.
- **T_network over UDP is real:** `T_network = mean(t_response − t_send)` measured at the intake (no cross-clock needed for this component — both endpoints are intake-clock events). Producer clock correction is needed only for `T_service` attribution inside `T_dispatch`.

### 3.3 Backend / LLM — staged A then C, avoid B (Decision 6)

**CLI — `demo/run_physical.py`:**
```bash
python -m demo.run_physical \
  --backend {deterministic,llm} \
  --llm-placement {on-pi,off-pi} \
  --edges 2 \
  --seeds 1-5 --k 8 \
  --leaf-latency-s 0.0 \
  --runs-per-cell 30 \
  --model-config deploy/pi/group_vars/llm/model.toml \
  --transport udp \
  --run-id <id> \
  --out demo/results/physical/<run-id>.jsonl
```

- **Stage A (ship first):** `--backend llm --llm-placement on-pi` — Ollama per producer on the Pi. Edge-realistic substrate share.
- **Stage C (add second):** `--backend llm --llm-placement off-pi` — off-Pi GPU host (e.g. DGX Spark on LAN), producer RPCs the model. Substrate share shrinks.
- **AVOID B** (dedicated on-LAN LLM *node* distinct from the producer): LLM RPC traffic would conflate `T_network` with `T_service`. Documented as rejected; not built.

**Paper story (verbatim framing):** the substrate's share of `T_intent` is X% at edge-realistic on-Pi latency, and shrinks further with larger off-board models → **network cost is bounded and predictable**.

**TOML contract** (`load_model_preference`, `pydantic_ai_backend.py:32-55`; local-first):
```toml
[model]
preference = ["ollama:llama3.2", "openai:gpt-4o-mini"]
```
- Stage A uses `["ollama:llama3.2"]`; Stage C may prepend a GPU-served endpoint.
- `--backend deterministic` never loads it; producers register `DeterministicBackend` (optionally wrapped in `LatencyBackend`).
- `--backend llm` constructs `PydanticAIBackend(...)` at producers. Import stays in `binding/pydantic_ai_backend.py` and is referenced only from `demo/` (AC3 preserved).

**Per-leaf split honestly stated:** in `--backend llm`, the dominant per-leaf time is inference. E records per leaf `inference_ms` (around `backend.invoke` at the producer), `transport_ms` (intake-clock `t_response − t_send`), and `total_ms`; the report must show the split and never attribute inference to the overlay. Cold-start inference goes through the disclosed trimming policy (§3.5).

### 3.4 Honest framing discipline (tiers, gates, namespaces)

**D and E are different fidelity tiers and never share a number (Decision 8):**

| | Experiment D | Experiment E |
|---|---|---|
| Transport label | `bus` | `udp` |
| Nature | SimulationBus **mechanism illustration** | **physical deployment** |
| Backend | `DeterministicBackend` only | `DeterministicBackend` (+ optional `PydanticAIBackend`) |
| T_network | **near-zero by construction** | **real, measurable** |
| Gate | `concurrent_publishable` | `physical_publishable` |
| JSONL namespace | `demo/results/concurrency.jsonl` | `demo/results/physical/<run-id>.jsonl` |
| Kind tag | `kind="concurrency_run"` | `kind="physical_run"` |
| Report section | "SimulationBus mechanism illustration" | "Physical deployment (N Pi 5, UDP)" |

**HARD RULE (Decision 8):** D and E numbers **never** share a table, figure, or derived ratio. Enforced by the namespace rule below and by a test.

**`physical_publishable` gate — exactly when True (v2, Decision 5):**
> `physical_publishable` is `True` **iff**:
> 1. `metadata.transport == "udp"` and the run executed on ≥ 8 distinct physical hosts (recorded host inventory + per-node run markers), with `--edges >= 1` recorded.
> 2. `metadata.reproducible is True` (clean tree) **or** `metadata.parameters["deployment"]` records exact `PICN_COMMIT` and `--run-id`.
> 3. The intake's `submit_intent` completed with `trace_root_verified is True`.
> 4. **Per-cell run count ≥ `MIN_PHYSICAL_RUNS` (=30, Decision 5)** for the cell, same `(seed, k, backend, llm_placement, leaf_latency_s, edges)`; report **n / median / mean / stdev**.
> 5. All five `T_intent` components present per run; if `backend="llm"`, the `inference_ms`/`transport_ms` split present for every leaf.
> 6. Host clock skew recorded and below threshold; runs above threshold excluded and counted.
> 7. Multi-edge cells: at least one leaf served by a **non-intake edge** (proves name-based routing, not local fan-out).
>
> Failing any → gate `False`, figures refuse to render, record retained for audit.

**Caption/claim rules (mandatory):**
- Every D caption: *"SimulationBus mechanism illustration on a single host; deterministic backends; T_network is near-zero by construction; not a deployment measurement."*
- Every E caption: *"Physical deployment on N Raspberry Pi 5 nodes over UDP at commit `<sha>`; measured wall-clock includes real transport and (for the LLM variant) model inference, reported separately; T_intent decomposed with producer-side service time clock-offset-corrected."*
- **Forbidden:** citing a D speedup/T_network in an E claim or vice versa; calling D a "deployment"; calling E "controlled"/"comparable to D"; collapsing D+E into one number; **plotting D and E on shared axes without explicit tier separation** (Decision 8).

**Namespace enforcement:** D aggregation reads only `kind="concurrency_run"`; E reads only `kind="physical_run"`. A shared summary keeps them in separate top-level keys, never averaged. A test asserts no figure consumes both kinds.

### 3.5 Statistical discipline (v2, Decision 5)

- **Runs per cell:** `--runs-per-cell ≥ 30` for **all** E cells (deterministic **and** LLM). 30 is the floor, not the ceiling.
- **Report always:** `n`, median, mean, stdev. Publishable requires the cell threshold met.
- **Jitter handling:** real UDP must **not** use the bus cold-start cap. Policy: (1) report the full sample mean ± stdev and median (primary); (2) report a trimmed variant only if a documented outlier rule fires (>3×IQR or cold-start first-run), **count trimmed disclosed**; (3) cold-start inference (LLM first call per producer) excluded from primary stats via a per-producer warm flag, exclusion count disclosed.
- **Clock discipline:** per-node monotonic clocks; cross-node intervals offset-corrected; residual skew recorded. Prefer intake-side timing (single-clock) as primary.
- **Environment recorded:** kernel, Python version, `PICN_COMMIT`, CPU governor, thermal state, network medium, idle state, edges count, LLM placement.

### 3.6 Provisioning structure (multi-edge)

```
deploy/pi/
  inventory.ini            # [intake] [edges] [producers] [llm] [observer] host groups
  group_vars/
    all.yml                # PYTHON_VERSION, PICN_REPO, PICN_COMMIT, VENV_PATH
    edges.yml              # per-edge listen ports + owned capability prefixes
    intake.yml             # seed/k/leaf_latency_s lists, edge endpoints, out_dir
    llm.yml                # model preference TOML path; stage A vs C endpoint
  host_vars/
    pi-01.yml … pi-08.yml  # role, IP, edge ownership, clock-offset method
  roles/
    picn_base/             # python3.14, clone @ PICN_COMMIT, pip install -e ".[dev]"
    picn_node/             # template node config (role, ports, peers, owned prefixes), systemd
    picn_measure/          # run intake runner, collect JSONL + service records to observer
  runbooks/
    bringup.md             # ordered commands
    teardown.md
    clock_sync.md          # chrony/NTP + measured residual skew
```

**Inventory (multi-edge, 2 edges):**
```ini
[intake]
pi-01 ansible_host=10.0.0.11 role=intake edges=edge1:9001,edge2:9002

[edges]
pi-02 ansible_host=10.0.0.12 role=edge edge_id=edge1 edge_port=9001 owned_prefixes=/cap/fwd/hospital/beds/h1,/cap/fwd/hospital/beds/h2,/cap/fwd/hospital/beds/h3,/cap/fwd/hospital/beds/h4
pi-03 ansible_host=10.0.0.13 role=edge edge_id=edge2 edge_port=9002 owned_prefixes=/cap/fwd/hospital/beds/h5,/cap/fwd/hospital/beds/h6,/cap/fwd/hospital/beds/h7,/cap/fwd/hospital/beds/h8

[producers]
pi-04 ansible_host=10.0.0.14 role=producer edge_id=edge1 capability="hospital/beds"
pi-05 ansible_host=10.0.0.15 role=producer edge_id=edge2 capability="hospital/beds"

[llm]
pi-06 ansible_host=10.0.0.16 role=llm llm_placement=on-pi model_endpoint="http://127.0.0.1:11434"

[observer]
pi-07 ansible_host=10.0.0.17 role=observer

[all:vars]
picn_commit=<sha>
python_version=3.14
edges=2
min_physical_runs=30
```

**Env contract (per node):** `PICN_ROLE`, `PICN_EDGE_ID`, `PICN_LISTEN_PORT`, `PICN_PEER_HOST`, `PICN_PEER_PORT`, `PICN_OWNED_PREFIXES`, `PICN_BACKEND`, `PICN_LLM_PLACEMENT`, `PICN_MODEL_CONFIG`, `PICN_RESULTS_DIR`, `PICN_RUN_ID`, `PICN_SEED`, `PICN_K`, `PICN_LEAF_LATENCY_S`, `PICN_EDGES`.

**Bring-up (structure):**
```bash
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml --tags base,clock
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml --tags deploy
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml --tags healthcheck
ssh pi-01 'cd $PICN_REPO && $VENV/bin/python -m demo.run_physical \
  --edges 2 --backend deterministic --seed 1 --k 8 --leaf-latency-s 0.05 \
  --runs-per-cell 30 --run-id e2-det-$(date +%s) --out-dir $PICN_RESULTS_DIR'
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml --tags collect
```

### 3.7 File plan (Experiment E v2)

| File | Action | Purpose |
|---|---|---|
| `demo/run_physical.py` | **new** | E runner CLI: `--backend`, `--llm-placement`, `--edges`, `--runs-per-cell`; writes `kind="physical_run"` with T_intent decomposition + latency split |
| `demo/physical_topology.py` | **new** | E wiring: intake `AgenticLayer` + one `PicnSubstratePort` per edge; producers with `Deterministic`/`Latency`/`PydanticAI` backends |
| `demo/physical_node.py` | **new** | Per-node entrypoint (edge/producer/llm/observer) via `PICN_ROLE`; systemd-friendly |
| `demo/collect_service_records.py` | **new** | Gather producer-side observer records; apply clock-offset correction |
| `agentic/benchmark/physical.py` | **new** | E snapshot + `physical_publishable` gate (pure Python, **no `PiCN.*`** → AC1 safe) |
| `deploy/pi/inventory.ini` | **new** | host inventory (multi-edge) |
| `deploy/pi/playbook.yml` + `deploy/pi/roles/**` + `group_vars/**` + `host_vars/**` | **new** | provisioning |
| `deploy/pi/runbooks/{bringup,teardown,clock_sync}.md` | **new** | exact commands + clock policy |
| `deploy/pi/group_vars/llm/model.toml` | **new** | model preference contract |
| `demo/generate_physical_report.py` | **new** | E report + captions; reads only `kind="physical_run"` |
| `agentic/tests/test_physical_gate.py` | **new** | gate conditions incl. ≥30 runs, multi-edge leaf, split present |
| `agentic/tests/test_run_physical_cli.py` | **new** | CLI contract, namespace isolation |
| `demo/README.md`, `docs/agentic_demo.md` | modify | document E, distinguish from D |

**AC compatibility:** `demo/`/`deploy/` are outside `agentic/` contracts. `agentic/benchmark/physical.py` stays pure (no `PiCN.*`, no LLM client).

---

## 4. Metric schema evolution v2 (full backward-compatible delta)

### 4.1 `MetricEvent` — frozen
`MetricEvent(kind, transport, seed, value, labels)` (`events.py:29-47`) unchanged. No new required fields. All new information rides in `labels` (`dict[str, Any] | None`).

### 4.2 `EventKind` — additive only
Append (never remove/reorder, `events.py:14-26`):
```
"t_intent_ms", "t_decompose_ms", "t_dispatch_ms", "t_network_ms",
"t_service_ms", "t_aggregate_ms", "t_dispatch_residual_ms",
"fanout_serial_ms", "fanout_concurrent_ms", "fanout_speedup",
"leaf_inflight_peak", "leaf_overlap_fraction", "leaf_start_ms", "leaf_end_ms",
"observer_overhead_ms", "config_snapshot",
```
Existing kinds untouched.

### 4.3 `MetricsSnapshot` — additive optional fields
New `None`-default fields (listed §2.3). Paper aliases added as read-only properties. `compute_metrics` gets only an additive tail block; existing outputs byte-identical when new kinds absent.

### 4.4 Coexistence table

| Metric family | Gate | Transport | Populated by | Status |
|---|---|---|---|---|
| `m1_latency_ms` | none (absolute from UDP only) | bus/udp | harness | unchanged |
| `m3_overhead_ratio` / `nfn_stack_overhead_ratio` | `m3_publishable` | bus | `run_paired_bus` | **untouched by D/E** |
| `t_intent_ms` + decomposition | `concurrent_publishable` (D) / `physical_publishable` (E) | bus / udp | D / E | new, tier-scoped |
| `concurrent_publishable` | new | bus | D | new, independent |
| `physical_publishable` | new | udp | E | new, independent |

Three gates are independent booleans; no gate implies another. Report generators check the gate matching their figure.

---

## 5. ADR A-012 — full draft

> This section is the complete draft to be placed at
> `/Users/dgre/ai-agency/gh-repositories/Agentic-ICN-Network/agentic_PiCN/decisions/A-012-name-correlation-and-wildcards.md`,
> following `TEMPLATE.md` and the A-011 style.

---

# A-012: Name correlation and wildcards — direction LPM plus an echoed correlation token

- **Status:** proposed
- **Date:** 2026-09-12
- **Decided by:** Dennis Grewe
- **Milestone:** M6 (Experiments D & E)
- **Relates to:** A-001 (substrate port interface), A-003 (capability naming), A-005 (Merkle trace root), [`spec/06-substrate-port.md`](../06-substrate-port.md) §4 (events not entries), Experiment D/E design v2 (§2.6, §3.1), open question 16

---

## Context

`PicnSubstratePort` demultiplexes uplink Content back onto outstanding Interests by scanning `self._outstanding` and matching names. The current match, `_match_outstanding` (`agentic/adapters/picn/port.py:262-269`), is **bidirectional**:

```python
for correlation, interest_name in self._outstanding.items():
    if interest_name == content_name or interest_name.is_prefix_of(content_name):
        return correlation
    if content_name.is_prefix_of(interest_name):
        return correlation
```

The second clause is the defect. It matches when the **Content name is a prefix of the Interest name** — the opposite direction to ICN semantics. Two consequences, both measured by reading the code, not assumed:

1. **Ambiguity / prefix collision.** Two outstanding Interests whose names are prefixes of one another (e.g. `/cap/fwd/hospital/beds/h1` and `/cap/fwd/hospital/beds/h10`) can each match the other's Content. The scan returns the **first** match in dict order, so the wrong `correlation` can be returned, `record_response` writes the wrong leaf digest, and the Merkle trace root silently attests a response that never happened. This is a **correctness bug surfaced by D/E**, not merely a measurement artifact.
2. **Aggregation and fan-in break exact matching.** Under an aggregation point that answers one Interest with a Content named for a *parent* prefix (or a future placeholder/wildcard symbol), exact-name matching cannot recover which outstanding request is satisfied. Today the bidirectional clause paper-overs this by accident — which is precisely why it is dangerous: it appears to work for the flat `h0…h7` names used in current demos.

The NFN paper defines the correct name operation as **directional longest-prefix match (LPM)**: a Content satisfies an Interest when the **Interest name's components are a prefix of the Content name's components** — never the reverse. PiCN already implements directional LPM for forwarding (`longest_prefix_match`, `port.py:167-175`, `prefix.is_prefix_of(name)`). The port's demux should agree with the forwarder.

Two facts bound the design:
- **`correlation` is already opaque and already echoed.** `SubstratePort.send_request` documents `correlation` as "opaque — echo it unchanged on every related event" (`port/protocol.py:92`), and adapters already echo it on `ResponseArrived` (`port.py:287-294`). The token mechanism exists in the agentic contract; it does **not** exist on the PiCN wire.
- **The Interest currently carries no parameters.** `PiCN/Packets/Interest.py:10-16` defines `Interest(name, wire_format)` with **no parameter field**, and `send_request` explicitly discards the payload on the wire (`_ = payload`, `port.py:210`). `Content` carries name + content only (`Content.py:10-18`). So binding a token to the outgoing Interest is a genuine **wire/parameter change**, not a local refactor.

## Options considered

### Option A — Exact-name matching only

| Pros | Cons |
|---|---|
| Trivial; removes the wrong-direction clause | Breaks under aggregation and any Content named for a parent prefix |
| No wire change | Cannot represent wildcard/placeholder satisfaction |
| | Does not fix the fundamental need: an aggregated Content has no 1:1 name |

Rejected: it trades a correctness bug for a capability loss, and aggregation is a core ICN premise.

### Option B — Directional LPM only

| Pros | Cons |
|---|---|
| Matches ICN/NFN semantics; agrees with the forwarder | Under aggregation, `h1..h4` Content named `/cap/fwd/hospital/beds` matches **every** outstanding `h*` — still ambiguous without a token |
| No wire change | Wildcard/placeholder symbols still ambiguous when names do not disambiguate |
| Removes the reverse-prefix bug | LPM alone cannot distinguish two requests whose Content names are identical prefixes |

### Option C — Exact correlation token when present, directional LPM as fallback

| Pros | Cons |
|---|---|
| Token is **name-independent** → correct under aggregation, fan-in, wildcards, placeholders | Requires a token on the Interest and an echo in the Content — a wire/parameter change and an adapter-wide touch |
| Falls back to correct LPM for plain-NDN peers that do not echo a token | Two matching modes to test; token must be namespaced and authenticated to avoid confusion |
| Correlation uniqueness already guaranteed by the agentic layer (`sha256(parent‖leaf)`, `layer.py:297-299`) | Larger implementation surface than A or B |

### Option D — Full wildcard-resolver table

| Pros | Cons |
|---|---|
| Complete solution for arbitrary wildcard/placeholder names | Substantial new state machine; no current requirement; untestable without a resolver spec |

Rejected **for now** — reclassified as **future work** (Decision 1). C already makes the demux correct under wildcards that the current stack produces.

## Decision

**Adopt Option C.**

1. Replace the bidirectional scan with **true directional LPM**: a Content satisfies an Interest iff `interest_name.is_prefix_of(content_name)` (Interest is a component-prefix of Content). The reverse clause is deleted.
2. **Bind a correlation token to the outgoing Interest** and **require it echoed in the Content.** Demux order: **(a) exact correlation match when the Content carries a token; (b) directional LPM fallback when it does not.**
3. Frame this as a **protocol feature** (an Interest/Content parameter addition), not a refactor. **Full wildcard-resolver-table support is future work.**

## Rationale

**Why not LPM alone (B).** Under aggregation, the aggregate point answers one Interest with a Content named for a shared prefix; directional LPM then matches that one Content to *all* outstanding requests under it. Name alone cannot identify the satisfied request. The token can, because it is independent of the name.

**Why not exact matching (A).** It cannot express aggregation at all and would regress a core ICN property the scenario depends on.

**Why the token is safe.** `correlation` is already owned by the agentic layer, already unique per leaf (`sha256(parent_intent_digest ‖ sub_intent_digest)`, `layer.py:297-299`), and already opaque to the adapter by contract (`protocol.py:92`). Echoing it in the Content is the natural completion of a contract the adapter already half-implements (`port.py:287-294`). Uniqueness is structural, not probabilistic.

**Why LPM remains the fallback.** A plain NDN forwarder or a peer that does not implement the token still interoperates: the demux degrades to the semantically correct directional LPM (B), never to the broken bidirectional match. This preserves the "interoperate with a plain forwarder" property A-003 depends on.

**Why the reverse clause is deleted outright, not kept as a third fallback.** It has no valid semantics under any ICN definition and is the direct cause of the reported corruption. Keeping it "just in case" reintroduces the bug for any Content whose name is a prefix of an outstanding Interest.

## Costs accepted

- **Wire/parameter change.** `Interest` currently has no parameters (`Interest.py:10-16`). Adding a token is a packet-format touch and must obey `AGENTS.md`'s *"do not change network behaviour while changing architecture"* rule — it must be an explicit, tested, versioned protocol change, **not** folded into a refactor commit. The token is optional by design (absent token → LPM fallback), so the change is **additive and backward-compatible** if encoded as an optional parameter.
- **Adapter-wide touch.** Every adapter that demuxes uplink Content (`PicnSubstratePort`, `MockSubstratePort`) and every producer that emits Content must participate in the echo contract, or fall back to LPM. `MockSubstratePort` currently demuxes by its own outstanding table; it must be made consistent.
- **Two matching modes to test.** The gate must cover token-present exact, token-absent LPM, and the prefix-trap rejection.
- **Not a complete wildcard solution.** Content matching a placeholder symbol where no token is echoed still falls back to LPM and may be ambiguous. Mitigation: the agentic layer always emits and requires the token on its own path; LPM is only the interop fallback. The resolver table is future work.
- **Encoder investigation required.** Whether `SimpleStringEncoder`/`BasicEncoder` can carry an optional Interest parameter without a non-backward-compatible format change is **not verified** (see Experiment D/E design v2 §0). This is the ADR's largest implementation risk and CRAFT's first investigation.

## Consequences

- `_match_outstanding` becomes deterministic: exact token match first, else directional LPM. Its return is no longer dict-order dependent when a token is present.
- `SubstratePort.send_request`'s documented "echo it unchanged" contract (`protocol.py:92`) becomes **enforced on the PiCN path**, not merely requested.
- The Merkle trace root's correctness under concurrency and aggregation now rests on a **name-independent** key — which is what makes Experiment D's out-of-order completion test meaningful and Experiment E's multi-edge routing defensible.
- A-003 (capability naming) must note that demux no longer keys on name shape; the resolver-table future work is recorded there or here as a follow-on.
- Implementers are constrained: **do not re-introduce reverse-prefix matching**, and **do not drop the token echo** on padded/aggregated Content. Both constraints belong in `AGENTS-design.md`.

## Research relevance

This decision is a **contribution**, not just a bug fix. The coupling of a name-independent correlation token with directional LPM is what lets the ICN overlay's accountability (Merkle trace root) remain correct under **aggregation and wildcard fan-out** — the precise regime where name-only demux is provably ambiguous. It converts the committee's "fan-out benefit untested" objection from a claim into an addressable mechanism, and it is the enabling change for Experiment E's physical multi-edge, name-based routing demonstration.

The honest boundary is recorded: the token makes the *agentic* path wildcard-ready; a general wildcard resolver remains future work.

## Verification

Runnable commands (to be implemented by CRAFT under `agentic/tests/`):

```bash
# 1. The prefix trap that the bug caused: h1 and h10 outstanding; a Content
#    for h10 must NOT match h1 (and vice versa). Fails on the current code.
python -m pytest agentic/tests/test_name_correlation.py::test_prefix_trap -v

# 2. Directional LPM: a Content named for a parent prefix matches the longest
#    outstanding Interest prefix in the correct direction only.
python -m pytest agentic/tests/test_name_correlation.py::test_directional_lpm -v

# 3. Token echo: a Content carrying the correlation token resolves to exactly
#    that outstanding request even under aggregation (shared-prefix) names.
python -m pytest agentic/tests/test_name_correlation.py::test_token_exact_match -v

# 4. Interop fallback: a Content with NO token still demuxes via directional LPM.
python -m pytest agentic/tests/test_name_correlation.py::test_lpm_fallback_no_token -v

# 5. Regression: existing port tests stay green.
python -m pytest agentic/tests/test_port.py agentic/tests/test_mock_adapter.py -v
```

The reverse clause is gone:

```bash
grep -n "is_prefix_of" agentic/adapters/picn/port.py
```
Expect the directional clause only; **no** `content_name.is_prefix_of(interest_name)`.

## Follow-on

- **A-013 (future, not yet drafted):** wildcard-resolver table — placeholder/wildcard symbol semantics and resolution.
- Encode the token as an **optional** Interest parameter and confirm the encoder round-trips it (`SimpleStringEncoder` investigation, CRAFT).

---

*End of ADR A-012 draft.*

---

## 6. Honesty & anti-strawman checklist v2

**Experiment D:**
- [ ] Serial baseline is the **actual** current `submit_intent` serial path, not a synthetic loop.
- [ ] Serial and concurrent use the **same** leaf set, `k`, `leaf_latency_s`, backend, seed.
- [ ] `leaf_latency_s > 0` for any **published speedup**; `leaf_latency_s=0` runs are **controls** and rendered separately (Decision 7), never as a speedup claim.
- [ ] Trace roots byte-identical between modes.
- [ ] Speedup **never** computed across different sweeps/leaf sets.
- [ ] **T_intent accounting is single-count:** View A (`T_decompose + T_dispatch + T_aggregate`) is exact; View B attributes `T_network`/`T_service` **inside** `T_dispatch` and reports `T_dispatch_residual`; the two are never silently added.
- [ ] **D's `T_network` is stated as near-zero by construction**, not as a network measurement.
- [ ] `T_service` is reported for D **only** for co-located producers; otherwise labelled `measured=false` / `service_unmeasured`.
- [ ] Observer overhead bounded by calibration (`observer=on` vs `off`) and reported (`observer_overhead_ms`); observer can be disabled.
- [ ] SimulationBus framing + "T_network near-zero by construction" on every caption.
- [ ] Out-of-order completion exercised (`hold_terminals` + reversed `release_terminals`) and root verified.
- [ ] Peak in-flight and overlap **measured**, not asserted.
- [ ] Speedup flattening at large `k` reported, not hidden.

**Experiment E:**
- [ ] ≥ 8 distinct physical hosts; `--edges` recorded; multi-edge cells have ≥1 leaf served by a non-intake edge.
- [ ] `transport=udp`; no SimulationBus in an E run.
- [ ] **`T_network` reported as real**, with its actual share of `T_intent`.
- [ ] LLM latency split (`inference_ms` vs `transport_ms`) present; overlay not credited with inference time.
- [ ] `--runs-per-cell ≥ 30` for every cell (det + LLM); n/median/mean/stdev reported.
- [ ] Clock skew measured, thresholded; excluded runs counted; producer `T_service` offset-corrected.
- [ ] Context PIT stated as intake-local, not distributed.
- [ ] Cold-start inference exclusion disclosed.
- [ ] `physical_publishable` enforced before any E figure renders.
- [ ] D and E never in the same table/figure/derived ratio (Decision 8).

**Cross-cutting anti-strawman:**
- [ ] D does not claim to beat Temporal/Step Functions; A/B handle production-system comparison. D/E measure the **substrate share of T_intent**.
- [ ] E does not claim superiority over D; it is a physical fidelity tier.
- [ ] No "we beat X" language.
- [ ] All gates documented in `EXPERIMENT_PLAN.md` and `demo/README.md`.
- [ ] `A-012` token echo enforced on the agentic path; reverse-prefix match absent.

---

## 7. Risks & open questions v2

**Resolved by the 8 decisions (v1 open questions closed):**
1. Port demux ambiguity (v1 Q1) → **Decision 1 / A-012** (directional LPM + token).
2. `LatencyBackend` home (v1 Q2) → **Decision 2** (`agentic/binding/latency.py`).
3. D latency source (v1 Q3) → **Decision 3** (producer-side `LatencyBackend` as service-time sweep; reframed as `T_service`).
4. E first configuration (v1 Q4) → **Decision 4** (`--edges N`, N=2 first).
5. `MIN_PHYSICAL_RUNS` (v1 Q5) → **Decision 5** (≥30, all cells).
6. LLM placement (v1 Q6) → **Decision 6** (staged A then C; avoid B).
7. Zero-latency control (v1 Q7) → **Decision 7** (yes, every D campaign).
8. D/E paper placement (v1 Q8) → **Decision 8** (one section, two tiers, never share numbers).

**Retained risks (still live):**
1. **`TaskGroup` vs `ExceptionGroup` (MEDIUM).** `AsyncLayerProcess.run` avoids `TaskGroup` to keep exceptions unwrapped (`AsyncLayerProcess.py:98-107`). D unwraps + re-raises; needs a test and a documented rule.
2. **Bounded queues (MEDIUM).** `DEFAULT_INBOUND_SIZE=64` and `MAX_CONTEXT_PIT_ENTRIES=256` (PROVISIONAL) interact with peak in-flight; `port.py:319` awaits on a full queue → possible serialization. D must report queue-bound effects at larger `k`; E confirms defaults on hardware.
3. **`LatencyBackend` placement conflates inference with transport if wrong (MEDIUM).** Pinned to producer backend; test must assert invocation inside `hub.invoke` (`layer.py:198`), not `send_request`.
4. **Interest parameters not yet transmitted (`_ = payload`, `port.py:210`) (MEDIUM).** A-012 makes this the central wire change; without it the token cannot ride the Interest.
5. **Clock domain mismatch for E (MEDIUM).** `PicnSubstratePort` uses `time.monotonic` (`port.py:75`); E needs offset correction + honest error bars.
6. **No regression guarantee on existing sweeps (LOW).** `dispatch="serial"` default + golden test re-running cardiac bus comparing root and dispatch count.

**New risks introduced by the v2 reframing:**
7. **Observer overhead biases `T_intent` (HIGH — new).** Timestamp capture inside the dispatch loop and per-event hooks add measurable cost, which is itself part of `T_decompose`/`T_aggregate`. **Mitigation:** `--observer {on,off}` calibration cell; report `observer_overhead_ms`; the observer is a no-op by default in production wiring. Gate requires the calibration to exist for a publishable decomposition.
8. **`T_service` vs `T_network` separation when co-located (HIGH — new).** On SimulationBus and on a single Pi running producer+intake, service and transport intervals overlap or are indistinguishable. **Mitigation:** D reports T_service only for co-located producers and flags `measured=false` otherwise; E separates them by clock-corrected producer-side timestamps. **Report the uncertainty explicitly.**
9. **Token echo requires producer participation (MEDIUM — new).** An aggregated or third-party producer that does not echo the token forces LPM fallback and can be ambiguous. **Mitigation:** agentic path always echoes; fallback is interop-only; test covers no-token case.
10. **Wire-format additivity unverified (MEDIUM — new).** Whether the encoder round-trips an optional Interest parameter without breaking plain NDN interop is **not verified**. **Mitigation:** CRAFT investigates first; if it requires a non-additive format change, escalate to Dennis before implementing (would change A-012's cost framing).
11. **Two fidelity tiers can still be conflated by a careless reader (LOW — new).** **Mitigation:** Decision 8 hard rule enforced in the report generator and by `test_physical_gate.py`/namespace isolation tests.

**Open questions remaining for Dennis (not covered by Decisions 1–8):**
1. **`MAX_CONTEXT_PIT_ENTRIES` / `DEFAULT_INBOUND_SIZE` as PROVISIONAL** — do we raise them for D's `k=8` concurrency and E's multi-edge, or report the ceiling as a finding (A-011 style)? Recommendation: report the ceiling, do not silently raise.
2. **A-012 encoder additivity** — if the optional-parameter change is not backward-compatible, is a protocol bump acceptable, or do we restrict the token to the agentic internal representation and accept LPM-only over the wire? This changes what E's multi-edge routing can demonstrate.
3. **Observer default in production wiring** — confirm `NullLatencyObserver` (off) is the default and D/E opt in explicitly (my recommendation).
4. **Clock-skew threshold for E** — default 1 ms proposed; confirm against the chrony/NTP accuracy achievable on the Pi fleet.

---

*End of DESIGN v2. No implementation code written — every Go/Python implementation item is CRAFT's. Every claim about existing code cites a verified file:line or is flagged in §0/§7. The A-012 draft is complete and ready for placement; the encoder-additivity risk (new risk 10) is the one item I could not verify from the code and it is the ADR's largest implementation unknown.*
</task_result>
</task>
---

# ARCO REVIEW OUTCOME (2026-09-12) — VERDICT: BLOCK

ARCO adversarially reviewed v2 against the code. Verdict: **BLOCK** — the design measures
a topology the code does not have. Key findings:

- **BLOCKER 1 — producer path is dead code.** `AgenticLayer._on_inbound_request`
  (`layer.py:179-211`) is the only caller of `hub.invoke` (`layer.py:198`), reached only via
  `InboundRequest`, which is produced only by adapter `inject_interest` — which has NO production
  caller. `demo/cardiac_bus_topology.py:150-169` answers hospital leaves from the edge **Content
  Store** via `MgmtClient.add_new_content`; `AgenticForwarder` is built with NO
  `register_capability`. Therefore `LatencyBackend` is never invoked, `T_service` is unmeasurable,
  and D's `concurrent_publishable` is structurally unreachable.
- **BLOCKER 2 — `send_response` never transmits.** `PicnSubstratePort.send_response`
  (`port.py:212-213`) only writes `self._response_payloads[correlation] = payload` — no Content
  packet, no wire. Class comment: "AgenticForwarder wiring lands in G.3" (`port.py:53-54`).
  E's return path does not exist; §3.1's "no adapter change needed" is false.
- **MAJOR 3 — A-012 token has no carrier.** Interest has no parameter field AND Content carries
  only name+content AND send_response builds nothing. Four missing carriers: Interest param
  encode, Interest param decode, InboundRequest plumbing, Content echo encode/decode. The
  "wildcard-ready today" claim is false; it is conditional on the wire change landing.
- **MAJOR 4 — A-012 verification grep wrong.** `longest_prefix_match` legitimately contains
  `is_prefix_of` (`port.py:172`), so the stated grep expectation is unachievable. Must assert
  ABSENCE of the reverse clause (`content_name.is_prefix_of`), not presence of the directional one.
- **MAJOR 5 — late/duplicate response crashes.** `_on_response` (`layer.py:213-220`) does not check
  `entry.terminated`; `record_response` raises `ValueError("entry already terminated")`
  (`context_pit.py:203-204`). Nack demux (`port.py:296-311`) fails "oldest outstanding" by dict
  insertion order — arbitrary under concurrency.
- **MAJOR 6 — TaskGroup failure semantics under-specified.** A sibling error cancels other leaf
  tasks with no `_on_failed` → `done` never resolves → `submit_intent` hangs until outer timeout.
- **MAJOR 7 — D's T_network AND T_service both near-zero/unmeasurable** in current topology; View B
  would absorb ~100% into T_dispatch_residual, making the "decomposition" vacuous.
- **MAJOR 8 — gates gameable.** `control=true` not cross-validated against `leaf_latency_s`;
  E condition 7 ("one non-intake leaf") does not prove name-based routing; clean tree ≠ deployed
  commit on Pis; namespace test mechanism unspecified.
- **MAJOR 9 — ≥30 runs/cell × LLM inference is days** with no cost estimate; `--leaf-latency-s 0.0`
  redundant in LLM cells.
- **MINOR 10 — T_decompose mis-sourced**; `t_emission` is not observable inside `submit_intent`.
- **MINOR 11 — `config_snapshot` as EventKind abuses the frozen schema** — use RunMetadata.
- **MINOR 12 — repo-convention violations**: bundle of 4 concerns must be separate commits/PRs.

ARCO preserved as GOOD: I2 analysis; index-keyed Merkle order-independence; the two fan-out paths
distinction; two-view accounting concept; A-012 reverse-prefix diagnosis; honesty apparatus.

# DECISION (Dennis, 2026-09-12) — PRODUCER PATH: OPTION C

**Chosen: Option C.** Build the real producer path as a DEDICATED, SEPARATE milestone/PR
("G.3 producer wiring") BEFORE D and E. This unblocks both experiments, keeps D on the REAL ICN
forwarding path (the claim the rejected paper lacked), and respects AGENTS.md ("one concern per
commit"). Required work (from ARCO BLOCKER 1 & 2 + MAJOR 3):
1. Interest→InboundRequest translation inside `PicnSubstratePort._deliver_uplink_packet` so a
   forwarded capability Interest reaches `AgenticLayer._on_inbound_request` → `hub.invoke`.
2. `PicnSubstratePort.send_response` must build a Content and enqueue it down the stack (wire it).
3. Correlation-token carriers (per A-012): Interest param encode/decode, InboundRequest plumbing,
   Content echo encode/decode. Decision on wire format pending (ARCO open Q2).
4. Idempotent late/duplicate response handling + correlation-carrying Nack (or documented limit).
5. Deterministic TaskGroup failure semantics (failed/cancelled leaf → NULL, done always resolves).
6. Separate commits/PRs: (G.3 producer wiring) → (A-012 demux LPM) → (A-012 token wire) → D → E.

**Still open:** ARCO open Q2 — is an Interest wire/parameter format bump acceptable if the encoder
cannot carry an optional parameter additively, or does the token stay agentic-internal and E ships
LPM-only (wildcard-readiness claim dropped)? Pending Dennis decision.

# DECISION (Dennis, 2026-09-12) — A-012 WIRE TOKEN: OPTION 3 (STAGED)

**Chosen: Option 3.**
- **NOW:** Fix the demux bug only — delete the reverse-prefix clause; adopt **true directional
  longest-prefix matching**. Correct for all concrete names (incl. the h1/h10 prefix trap).
  No wire change. Perfect plain-NDN interop.
- **LATER (separate ADR/milestone):** The in-band correlation token that makes demux correct under
  aggregation/fan-in/wildcards. Requires a genuine Interest/Content wire-format change (new
  parameter field + encoder write + decoder read + the four carriers ARCO listed).

## GROUND TRUTH ON THE WIRE FORMAT (verified)
- `Interest(name, wire_format)` has no parameter field (`Interest.py:10-16`).
- `Content(name, content, wire_format)` carries name + content only (`Content.py:10-18`).
- `NdnTlvEncoder.encode_interest` writes a **random 4-byte Nonce** TLV + name only
  (`NdnTlvEncoder.py:149-166`); `decode_interest` returns only the Name, discarding the nonce
  (`NdnTlvEncoder.py:271-278`). PiCN's encoder does not read/write Interest parameters at all,
  even though the NDN packet format spec defines them.

## MANDATORY TRANSPARENCY NOTE (Dennis) — WIRE-FORMAT LIMITATION
Dennis: "the ICN community is really concerned about enhancing the wire format, so this is a
challenge to solve for future work and to be transparently mentioned in the paper."

Therefore, the following limitation MUST appear, explicitly and prominently, in:
(a) ADR A-012 (as the primary "Costs / Limitations" item),
(b) the design doc's honesty section,
(c) the PAPER (a named limitation in the discussion AND a forward-looking statement).

**Limitation text (canonical):**
> In-band correlation tokens require extending the ICN wire format (an Interest/Content parameter
> that is echoed by producers). The ICN community treats wire-format changes with strong caution,
> because even additive TLVs raise interoperability, cache-consistency, and deployment concerns
> across a global forwarding fabric. This work therefore deliberately ships **directional
> longest-prefix matching** — which is correct for concrete capability names — and presents the
> token as a **forward-looking design** (ADR A-012), not a deployed mechanism. Resolving wildcard
> and aggregation demux without widening the wire format remains an open problem and is called out
> as future work.

**Framing rule for the paper:** state the limitation plainly and early enough that no reviewer
discovers it as a surprise. Do NOT claim present-tense wildcard-readiness. Present it as
"current mechanism is correct and wire-compatible; the token design is our proposed direction,
with the wire-format tension acknowledged as an open challenge."
