<task id="ses_f6b571bd3ffe1kLBSSxd9NpyXy" state="completed">
<task_result>
I now have everything needed. Here is the design document.

---

# DESIGN — Experiments D & E: Concurrent Fan-Out and Physical Testbed for Agentic Routing

**Author:** DESIREE 🏗️ (architecture & design)
**Status:** DESIGN (not implemented)
**Repo:** `gh-repositories/PiCN` (fork of Basel PiCN)
**Target:** revise COMMAG-26-00578 (rejected) + TNSM/Computer Networks reframing
**Date:** 2026-09-12

---

## 0. Verification notes (what I read, what I could NOT verify)

**Verified by reading:**
- `submit_intent` leaf loop is serial: `agentic/agentic_layer/layer.py:296-311` — a plain `for … await self._port.send_request(...)`. No `asyncio.gather`, no `TaskGroup`. Confirmed via `rg` (only `AsyncLayerProcess.py:100` mentions TaskGroup, and explicitly avoids it).
- I2 holds structurally: `self._pit.commit(...)` at `layer.py:284-290` runs **before** the forward loop at `layer.py:296`. The commit is a synchronous, non-awaiting call — so any concurrency added **after** the loop cannot reorder commit relative to send.
- Leaf→index mapping is keyed by `correlation` in `self._leaf_index` (`layer.py:300`), and response recording is `self._pit.record_response(parent, leaf_index, digest)` (`layer.py:213-220`). **Index-keyed, not completion-order-keyed** → out-of-order completion is already safe. The Merkle tree is fixed-size and addressed by leaf index (`context_pit.py:207` `entry.tree.set_leaf(leaf_index, …)`), so concurrency does not reorder the tree.
- `DeterministicBackend.invoke` (`binding/deterministic.py:40-46`) may return an awaitable and awaits it, but the current cardiac handler (`scenario.py:222-226`) is synchronous and resolves instantly → zero latency.
- Cardiac `_run_exchange` (`scenario.py:346-482`) awaits each hospital serially: `response = await self._issue_hospital_response(...)` at `scenario.py:400` inside the `for` loop. **This is the structural scenario and does not use `AgenticLayer.submit_intent` at all** — it drives `ContextPIT` directly. Important distinction for D (see §2.0).
- `MetricEvent` is `(kind, transport, seed, value, labels)`; `EventKind` is a closed `Literal` (`benchmark/events.py:14-26`). `labels` is `dict[str, Any] | None` — new dimensions can ride in `labels` **without** touching `EventKind`.
- Publishability gate pattern: `_baseline_is_publishable` (`metrics.py:19-28`) → `m3_publishable` field (`metrics.py:54`); paper alias `nfn_stack_overhead_publishable` (`metrics.py:62-65`).
- Transport metadata is a closed `Literal["bus","udp"]` (`metadata.py:18`); A-011 capacity guard is bus-only (`capacity.py:24-34`).
- AC1 forbids `agentic.benchmark` from importing `PiCN.*` (`test_architecture.py:52-58`). **Constraint for E: the physical runner must live under `demo/`, not `agentic/benchmark/`.**
- AC3 forbids LLM clients in `agentic.port`, `agentic.adapters`, `agentic.agentic_layer`, `agentic.trust` (`test_architecture.py:62-69`). `pydantic_ai` lives only in `binding/pydantic_ai_backend.py`.
- `PicnSubstratePort` already supports real UDP: `UDP4Interface(0)` default and `AddressInfo((peer_host, peer_port), 0)` when `peer_port is not None` (`adapters/picn/port.py:76-107`). So E's UDP path is **already implemented**; it is untested on hardware, not unbuilt.
- `PydanticAIBackend` reads `[model] preference = [...]` via `load_model_preference` (`pydantic_ai_backend.py:32-55`); local-first, `["test"]` default when path is `None`.
- `MockSubstratePort` delivers terminal outcomes synchronously inside `send_request` unless `hold_terminals=True` (`mock/port.py:269-273`). For D's concurrency measurement this matters: with instant terminals, the leaf loop has nothing to overlap **even if concurrent** — hence the LatencyBackend requirement.

**Could NOT verify (flagged, no guessing):**
- **No `LatencyBackend` exists anywhere.** Grep for `latency_s`, `LatencyBackend`, `asyncio.gather`, `asyncio.TaskGroup` in `*.py` returned only unrelated matches. D must introduce it.
- **No Raspberry Pi / Ansible / inventory artifacts exist in-repo.** No `deploy/`, `inventory`, or `hosts.yml` found. E's provisioning structure is greenfield design.
- **`runs per cell` / statistical policy for UDP does not exist yet.** The only trimming logic is `_trim_cold_start` in `demo/generate_commag_report.py:42-45` (cap 500 ms), applied to SimulationBus numbers. There is no UDP jitter policy.
- Whether `PicnSubstratePort.send_request` is safe under many concurrent in-flight Interests on real UDP: the `_outstanding` dict and `_match_outstanding` (`port.py:262-269`) do a **linear, ambiguous prefix match** — two outstanding Interests with prefix-overlapping names could collide. This is an existing risk surfaced by D/E; flagged in §6.
- `MAX_CONTEXT_PIT_ENTRIES=256` (`context_pit.py:28`) and `DEFAULT_INBOUND_SIZE=64` (`layer.py:47`) are marked PROVISIONAL; D's peak-in-flight and E's node count interact with these bounds (flagged §6).

---

## 1. Goal & reviewer-mapping

### 1.1 The single skeptical-committee complaint this must kill

> "The strongest ICN win is untested. Everything is a single in-flight intent on a shared bus. The actual benefit of name/fan-out in an ICN/agentic overlay is untested."

Experiments D and E jointly attack this from two directions:

- **D** answers it **quantitatively and reproducibly**: does concurrent fan-out over the leaf send path actually overlap, and by how much, as `k` grows? This is the missing measurement: a speedup-vs-k curve on the exact leaf set, with a paired serial baseline on the *same* leaves.
- **E** answers it **physically**: does the same fan-out work over real UDP across 8+ Raspberry Pi 5 nodes, with a `LatencyBackend` (deterministic) *and* an optional real-LLM variant, honestly labelled as a physical deployment and never conflated with D.

### 1.2 Reviewer complaint → experiment mapping

| Complaint | Source | Answered by | How |
|---|---|---|---|
| R1 #6 / #7 — weak comparison vs Temporal / Step Functions / SPIFFE / OTel / MCP; "what does Agentic Routing add?" | R1 | **D** (+ existing A/B/C) | D adds the *mechanism-differentiating* measurement: concurrent name-based fan-out with per-leaf accountability (I2 preserved, Merkle intact under out-of-order completion). A/B/C compare *what you get*; D/E measure *the ICN win itself*. |
| R1 #3 / #4 — Context PIT omission-accountability and attestation limits underspecified | R1 | **D** (partial), **E** (partial) | D stress-tests omission accounting under out-of-order completion at k up to 8. E exercises it across real nodes. Deeper attestation-limit work remains C's remit. |
| R3 #3 / R4 — no implementation / simulation / PoC of a single mechanism | R3, R4 | **D** AND **E** | D is a contained, reproducible simulation PoC (SimulationBus, deterministic backend). E is a contained *physical* PoC (8+ Pi 5, real UDP). Together they convert "no PoC" into "two PoCs at different fidelity levels." |
| R4 — emergency scenario too high-level | R4 | **E** | E instantiates the cardiac emergency scenario as a real multi-node deployment with named roles (ambulance intake, hospital forwarders, optional LLM-serving Pi). |
| Skeptical committee — ICN name/fan-out benefit untested | committee | **D** (primary), **E** (secondary) | D's speedup-vs-k curve is the direct answer. E shows it under real transport. |

### 1.3 Non-goals (explicit)

- D does **not** claim real network behaviour. It is SimulationBus illustration.
- E does **not** claim a controlled performance comparison against D. It is a physical deployment feasibility study.
- Neither experiment touches the NFN combine microbenchmark (`m3` / `nfn_stack_overhead`). Those remain separate and untouched.

---

## 2. Experiment D — Concurrent fan-out (SimulationBus, DeterministicBackend only)

### 2.0 Critical scope clarification (grounded in code)

There are **two** fan-out paths in the codebase, and they are different code:

1. **`AgenticLayer.submit_intent`** (`layer.py:296-311`) — the *real* forwarding path over a `SubstratePort`. This is the path the skeptical committee means by "single in-flight intent on a shared bus." **This is the primary target of D.**
2. **`CardiacScenario._run_exchange`** (`scenario.py:396-408`) — the *structural* in-process path that drives `ContextPIT` directly, bypassing `submit_intent` and the port entirely. It also awaits each hospital serially.

**Decision:** D targets the **AgenticLayer path** (`submit_intent`) because that is the forwarding path whose concurrency is the actual ICN claim, and because it exercises `SubstratePort` (the same port E uses physically). The `CardiacScenario` path is **not** modified for concurrency in D; it stays the structural/in-process runner. If a concurrent structural variant is wanted later, it is a separate experiment. This keeps the change surface minimal and preserves the existing A/B/C contract (which reuse `CardiacScenario`).

### 2.1 Concurrency mechanism + exact insertion points

**Mechanism: `asyncio.TaskGroup`** (not bare `gather`). Rationale:
- Python 3.14 target (per `AGENTS.md`). `TaskGroup` gives structured cancellation: if one `send_request` raises, siblings are cancelled and the error propagates — safer than `gather` which leaves stragglers.
- BUT: `AsyncLayerProcess.run` explicitly avoids `TaskGroup` because it wraps single exceptions in `ExceptionGroup` (`PiCN/Processes/AsyncLayerProcess.py:98-107`). The agentic layer's `_event_loop` (`layer.py:159-165`) relies on `CancelledError` propagating cleanly. Inside `submit_intent`, however, `send_request` is documented **not to raise across the port boundary** (`port/protocol.py:11`, adapters emit `RequestFailed` events instead). So a `TaskGroup` here will not see child exceptions in normal operation, and `ExceptionGroup` risk is low. To be conservative and preserve the "adapters never raise across this boundary" contract, D runs the leaf dispatches in a `TaskGroup` **and additionally validates** that no leaf task propagates an exception; if an `ExceptionGroup` surfaces, it is unwrapped to the first leaf and re-raised — matching the ADR-007 unwrap discipline (design note for CRAFT).

**Exact insertion point — `submit_intent`:**

Current code (verified `layer.py:295-313`):
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

Design change (no code — description only):
- Introduce a `dispatch: DispatchMode` parameter on `submit_intent` (`"serial"` default, `"concurrent"` opt-in). Default preserves existing behaviour bit-for-bit.
- The **commit** at `layer.py:284-290` and the `done` future creation at `layer.py:291-293` remain **untouched and before** the loop. I2 is preserved because commit is synchronous and precedes both the serial and the concurrent loop.
- Before the dispatch loop (serial and concurrent alike), precompute for **every** leaf: `correlation`, `self._leaf_index[correlation] = (parent, leaf_index)`, `self._pit.mark_forwarded(parent, leaf_index)`, and the wire `name`. This is a synchronous preparatory pass. **Rationale:** `_leaf_index` and `mark_forwarded` must be registered **before any send** in both modes, otherwise a fast response could arrive before the mapping exists (`_on_response` returns early on `mapping is None`, silently dropping the leaf as PENDING → NULL at terminate, corrupting the trace). In the serial version this happens interleaved; the preparatory pass makes it happen up-front, which is strictly safer and identical in effect.
- **Serial path:** unchanged loop over the precomputed `(name, leaf, correlation)` tuples.
- **Concurrent path:** one `async with asyncio.TaskGroup() as tg:` block, one `tg.create_task(self._port.send_request(...))` per leaf. All sends are scheduled before any is awaited; the event loop interleaves their awaits.

**Invariant preservation argument (for the paper's accountability section):**
- **I2 (commit-before-forward):** commit is synchronous and precedes the loop; no `await` occurs between commit and the first dispatch. Unchanged by concurrency.
- **Merkle trace root semantics:** the tree is index-addressed (`context_pit.py:207`, `set_leaf(leaf_index, …)`), and `_on_response`/`_on_timeout`/`_on_failed` look up `leaf_index` by correlation (`layer.py:213-237`). Completion order never enters the digest computation. Out-of-order completion produces the **same** root as serial. D adds a **deterministic regression test**: run the same intent serial and concurrent and assert `trace_root` is byte-identical.
- **`maybe_complete` is race-safe?** `_on_response` calls `maybe_complete(...)` which calls `pit.terminate(...)` — idempotent (`context_pit.py:225-226` returns if already terminated) and the `done` future is guarded by `if not fut.done()` (`layer.py:244`). Since the event loop is single-threaded and all mutations are synchronous (no `await` inside `_on_response` between the lookup and `record_response`), there is no interleaving hazard. **This is a design claim to be proven by D's out-of-order test, not assumed.**

### 2.2 LatencyBackend — construction, placement, determinism

**Decision: a decorator wrapper `LatencyBackend` in `agentic/binding/latency.py`, implementing `CapabilityBackend`, delegating to any inner backend, and adding `await asyncio.sleep(latency_s)` before/after delegation.**

Why a wrapper (not a new DeterministicBackend variant, not a `latency_s` on DeterministicBackend):
- **Does not modify `DeterministicBackend` at all** → zero risk to existing determinism and to the AC6/binding tests.
- **Backend-agnostic**: it wraps `DeterministicBackend` in D and `PydanticAIBackend` in E (E measures LLM inference latency, where the wrapper is either bypassed or used to add transport-independent delay).
- Implements the same `CapabilityBackend` Protocol (`declared_schemas` + `invoke`) → registration and the forwarding path **never branch on backend type** (explicit invariant in `binding/protocol.py:18-20`).

**Construction (design contract):**
- New file `agentic/binding/latency.py`.
- `class LatencyBackend:`
  - `__init__(self, inner: CapabilityBackend, *, latency_s: float, jitter_s: float = 0.0, seed: int | None = None, clock: Callable[[], float] | None = None, measured: bool = True)`
  - `declared_schemas()` → delegate to `inner.declared_schemas()`.
  - `invoke(payload, *, deadline)` → `await asyncio.sleep(self._delay())`, then `await inner.invoke(payload, deadline=deadline)`, then optionally record measured inference time.
  - `_delay()`: if `jitter_s == 0`, return `latency_s` exactly (deterministic). If `jitter_s > 0`, use a **seeded** `random.Random(seed)` to draw `uniform(latency_s, latency_s + jitter_s)`. D uses `jitter_s=0` (deterministic, reproducible). E uses `jitter_s>0` to emulate real per-node/inference spread and records the drawn value.
- **Placement:** export from `agentic/binding/__init__.py` alongside `DeterministicBackend` (it has no PiCN import, so AC1/AC2 are unaffected; it does not import an LLM client, so AC3 is unaffected).
- **Where it is constructed in D:** in the D-only harness/scenario wiring under `demo/`, wrapping the deterministic handler at registration time:
  `register_capability(desc, LatencyBackend(DeterministicBackend(handler, …), latency_s=cfg.leaf_latency_s), …)`.
  `AgenticLayer.register_capability` (`layer.py:111-124`) is unchanged — the wrapper is invisible to the fabric.

**Critical subtlety — the `AgenticLayer` producer path vs the `CardiacScenario` path:** `LatencyBackend` adds latency where the backend is *invoked*. In the `submit_intent` path, the leaf `send_request` round-trip latency is a property of the **port/transport**, not the backend, unless the remote producer's backend sleeps (the AgenticForwarder producer path invokes the backend at `layer.py:198-200`). For D on SimulationBus, D must therefore give the **remote producer** (the edge `AgenticForwarder`/port registration) a `LatencyBackend`. If instead D wants to keep everything at the ambulance port, it can use a **latency-injecting test port** wrapping `MockSubstratePort`/`PicnSubstratePort` — but that is a *port* delay, not a *backend* delay, and conflates transport with inference. **Decision:** D uses `LatencyBackend` on the produced side (matching the paper claim "per-leaf capability service latency"), and documents it. A `LatencyPort` alternative is explicitly rejected for D to keep the measured quantity honest.

**Does not change network behaviour:** `LatencyBackend` changes *timing*, never packet formats, names, forwarding semantics, PIT/FIB/CS logic. It is off by default (only constructed by D/E runners). `AGENTS.md`'s "do not change network behaviour while changing architecture" is respected: D changes scheduling inside `submit_intent` and adds an optional backend wrapper; no wire/forwarding semantics change.

### 2.3 Concurrency metrics and new MetricEvent fields

**Backward compatibility rule:** `MetricEvent` (`events.py:29-47`) keeps `(kind, transport, seed, value, labels)` exactly. **No new required fields.** All new dimensions ride in `labels`. This preserves every existing test (`test_m3_publishable.py`, `test_harness_capacity.py`, etc.) and the JSONL schema.

**New `EventKind` values** (append to the `Literal` in `events.py:14-26`; purely additive, backward-compatible because existing kinds are untouched):

| New `EventKind` | `value` | `labels` | Meaning |
|---|---|---|---|
| `fanout_serial_ms` | wall ms | `{k, leaf_latency_s, mode:"serial", campaign}` | Paired serial dispatch wall-time for the leaf fan-out |
| `fanout_concurrent_ms` | wall ms | `{k, leaf_latency_s, mode:"concurrent", campaign}` | Concurrent dispatch wall-time (same leaf set, same k) |
| `fanout_speedup` | `serial/concurrent` ratio | `{k, leaf_latency_s}` | Speedup; only emitted in paired runs |
| `leaf_inflight_peak` | count | `{k, mode}` | Peak number of simultaneously in-flight leaf requests |
| `leaf_overlap_fraction` | 0.0–1.0 | `{k, mode}` | Fraction of leaf-pairs whose [start,end] intervals overlap |
| `leaf_start_ms` | start offset ms | `{k, mode, leaf_index}` | Per-leaf start (for overlap computation; optional, debug) |
| `leaf_end_ms` | end offset ms | `{k, mode, leaf_index}` | Per-leaf end (optional, debug) |

**Where measurement happens.** D wraps the dispatch loop with `time.perf_counter()`:
- `t0` immediately before the dispatch block; `t1` immediately after the block completes (i.e., after all `send_request` coroutines have returned, in both modes). **Serial wall-time** = `t1-t0` for `mode="serial"`; **concurrent wall-time** = `t1-t0` for `mode="concurrent"`. These are *dispatch* times, not end-to-end intent time (the end-to-end `await done` time is captured separately as `agentic_latency`, unchanged).
- **Peak in-flight:** in the concurrent path, an `asyncio` counter incremented on entry to a wrapper coroutine around each `send_request` and decremented on exit; the max observed is the peak. For the serial path, peak is trivially 1 (asserted, not measured).
- **Overlap fraction:** collect `(start,end)` per leaf via the wrapper; compute pairwise interval overlap; store as a fraction. This is D's direct answer to "do they actually overlap."

**Coexistence with `m3_publishable` / `nfn_stack_overhead`:** the new kinds are **ignored** by `compute_metrics` (`metrics.py:73-132`) because they are not named in any of its list comprehensions. D's aggregation logic lives in a **new module** `agentic/benchmark/concurrency.py` (a peer of `metrics.py`), which reads D's events and computes the D-specific snapshot. `compute_metrics` remains untouched by D except for one additive helper (see §4). The existing `m3_publishable` computation is unaffected.

### 2.4 Sweep specification

**Sweep axes:**
- `--concurrency {serial,concurrent}` — default sweep runs **both** (paired).
- `--k 2,3,4,5,8`.
- `--seeds 1-5` (configurable).
- `--leaf-latency-s` (float, e.g. `0.02`) — the per-leaf `LatencyBackend` delay. Required for a meaningful concurrency measurement.
- `--path happy` (D uses happy only; adversary is C/B territory).
- `--transport bus` (SimulationBus only — `transport=udp` is E's namespace).

**Paired-run semantics:** for each `(seed, k)` cell, D runs **serial first, concurrent second, on the same leaf set and same `leaf_latency_s`**, producing a single JSONL record containing both times and the derived speedup. A cell is only valid if both modes completed with identical leaf sets (same `k`) and byte-identical trace roots.

**CLI — new `demo/run_concurrency.py`** (mirrors `demo/run_sweep.py` style; argparse + `asyncio.run`):

```bash
python -m demo.run_concurrency \
  --seeds 1-5 \
  --k 2,3,4,5,8 \
  --concurrency serial,concurrent \
  --leaf-latency-s 0.02 \
  --transport bus \
  --out demo/results/concurrency.jsonl \
  --summary-json demo/results/concurrency_summary.json \
  --allow-dirty
```

Output: one JSONL record per `(seed,k)` with both mode timings, speedup, peak in-flight, overlap fraction, trace-root equality boolean, and run metadata. A summary JSON emits the **speedup-vs-k curve** (mean ± stdev) and the **serial/concurrent wall-time-vs-k** curves.

**Expected result shape (feasibility, not a claim):** for `k` leaves each with fixed `L` latency, ideal concurrent dispatch wall-time ≈ `L` (plus fan-out scheduling overhead) while serial ≈ `k·L`, so `speedup ≈ k` until `k` approaches the executor/queue bound. The curve should be near-linear for small `k` and flatten as scheduling overhead and port bounds appear. D is the first experiment to produce this.

### 2.5 Publishability gate — `concurrent_publishable`

Mirror `_baseline_is_publishable` (`metrics.py:19-28`). Add to `MetricsSnapshot` (or the new D snapshot) a boolean **`concurrent_publishable`** whose definition is **exactly**:

> `concurrent_publishable` is `True` **iff all** of the following hold for a cell:
> 1. Both `fanout_serial_ms` and `fanout_concurrent_ms` events exist for the **same** `(transport="bus", seed, k, leaf_latency_s)`.
> 2. Both events carry `labels["leaf_latency_s"]` equal and `> 0` (a zero-latency run cannot demonstrate concurrency and must not be published as a speedup).
> 3. The serial run and the concurrent run used the **same leaf set**: identical `k` and identical `expected_subintent_set` (compared via recorded digests, not just `k`).
> 4. The trace root is byte-identical between the two modes for that cell (`trace_root_equal is True`) — proving concurrency did not alter accountability.
> 5. `fanout_speedup` is finite and `> 0`.
>
> If any condition fails, `concurrent_publishable=False` and the figure layer **refuses to render** the speedup curve (same discipline as `_plot_bar_ratios` reading `m3_publishable`).

**Anti-inflation rule:** a speedup may only be reported from a **genuinely paired** serial-vs-concurrent run on the same leaf set, same `k`, same backend latencies. Computing "speedup" by dividing an independently-measured serial mean by an independently-measured concurrent mean from a **different** sweep is forbidden and must be blocked by the gate (condition 1 requires both events in the same cell record).

### 2.6 File plan (Experiment D)

| File | Action | Purpose |
|---|---|---|
| `agentic/binding/latency.py` | **new** | `LatencyBackend` wrapper (deterministic, seeded jitter optional) |
| `agentic/binding/__init__.py` | modify | export `LatencyBackend` |
| `agentic/agentic_layer/layer.py` | modify | add `dispatch: DispatchMode="serial"` to `submit_intent`; precompute correlations/names + `mark_forwarded` before dispatch; `TaskGroup` concurrent branch; dispatch timing + in-flight counter (behind an optional injected `DispatchObserver`) |
| `agentic/agentic_layer/dispatch.py` | **new** | `DispatchMode` enum + `DispatchObserver` Protocol (timing/peak/overlap hooks; no-op default) — keeps measurement out of the hot layer core |
| `agentic/benchmark/events.py` | modify | append new `EventKind` values (additive) |
| `agentic/benchmark/concurrency.py` | **new** | D snapshot: serial/concurrent wall-time, speedup, peak in-flight, overlap; `concurrent_publishable` gate |
| `agentic/benchmark/__init__.py` | modify | export new snapshot/gate |
| `demo/run_concurrency.py` | **new** | D runner CLI (paired serial/concurrent sweep) |
| `demo/concurrency_topology.py` | **new** | D-only wiring: ambulance `AgenticLayer` + edge producer with `LatencyBackend`, SimulationBus, records dispatch observer events |
| `demo/generate_concurrency_report.py` | **new** | speedup-vs-k figure + markdown/caption (honest SimulationBus framing) |
| `agentic/tests/test_concurrency_paired.py` | **new** | serial vs concurrent on same leaves → identical trace root, speedup > 1 with latency, peak in-flight, overlap |
| `agentic/tests/test_latency_backend.py` | **new** | wrapper determinism, jitter seeding, schema delegation, `CapabilityBackend` conformance |
| `agentic/tests/test_concurrent_publishable.py` | **new** | gate true only on paired same-leaf-set runs; false on unpaired/zero-latency/differing-root |
| `agentic/tests/test_submit_intent_i2_concurrent.py` | **new** | out-of-order completion (via `MockSubstratePort(hold_terminals=True)` + `release_terminals` in reversed order) → correct Merkle root, no dropped leaves |
| `demo/README.md`, `demo/EXPERIMENT_PLAN.md` | modify | document D, link runner, state gate |

**Note:** `layer.py` must not import `PiCN.*` beyond `PiCN.Processes` / `PiCN.Packets` (AC2). `asyncio`, `DispatchObserver`, and `DispatchMode` are stdlib/agentic → safe. `dispatch.py` lives under `agentic.agentic_layer` and imports no PiCN → safe.

---

## 3. Experiment E — Physical testbed (8+ Raspberry Pi 5, UDP, optional real-LLM variant)

### 3.1 Topology for 8+ Pi 5

**Roles (minimum 8 nodes):**

| # | Role | Node software | Interfaces |
|---|---|---|---|
| 1 | **Intake / Ambulance** | `AgenticLayer` + `PicnSubstratePort` (UDP client to the edge) + application driver | UDP toward edge |
| 2–3 | **Edge / Hospital forwarders** (2–3 nodes) | `AgenticForwarder` (async) with registered capability producers (`hospital/beds`), each backed by `DeterministicBackend` or `LatencyBackend` | UDP face(s) toward intake and toward peers; optional second face for content |
| 4–5 | **Capability producers / hospitals** (0–2 dedicated, remainder folded into edge) | `AgenticForwarder` producers registering descriptors + backends | UDP |
| 6 | **LLM-serving Pi** (optional, E-LLM variant only) | `PydanticAIBackend` host: Ollama (`ollama:llama3.2`) locally; no forwarding path | UDP (as a producer behind an edge forwarder) |
| 7 | **Observer / measurement node** (optional) | Records per-run wall-times, jitter, node clocks; not on the data path | mgmt/UDP |
| 8+ | **Spare / scale-out** | additional edge/hospital forwarders to exceed k=8 | UDP |

**Naming and forwarding over real UDP faces:**
- Capability Interests keep the existing wire form from `submit_intent`: `/cap/fwd/<capability-path…>` (`layer.py:303-305`; `cardiac_bus_topology.py:83-84`). No new naming.
- `PicnSubstratePort` is constructed with `peer_host=<edge IP>, peer_port=<edge UDP port>` — the real-UDP branch at `adapters/picn/port.py:100-107`. **No adapter change needed for basic UDP.**
- The intake node's port sends Interests to the edge; the edge `AgenticForwarder` receives them via its `UDP4Interface` and routes to registered producers. Producer responses return as Content and are demuxed by `_match_outstanding` (`port.py:262-269`).
- **Faces:** each node has at least one UDP face; the intake ↔ edge link is the critical path. Multiple edges can be reached by running one `PicnSubstratePort` per edge (the intake fans out over multiple ports) — this is the physical manifestation of "concurrent name fan-out." **Design decision:** for the first E run, use **one edge with multiple registered producers** (simplest, matches the D topology) and add multi-edge fan-out as a second configuration (`--edges N`).

**Distributed / aggregated Context PIT (design statement — honest):**
- The Context PIT today is **in-memory and node-local** (`context_pit.py`, `AgenticLayer._pit`). It is **not** distributed in the current code.
- **E's claim is limited accordingly:** the **intake node** holds the authoritative Context PIT for a parent intent and aggregates responses from remote producers; the Merkle trace root is computed **at the intake**. Remote producers hold no Context PIT for the parent (they serve a capability). This is the correct, defensible framing and must be stated verbatim in the report.
- **Aggregation across nodes:** results are gathered at the intake via the port; `record_response` is called on the intake's Context PIT as `ResponseArrived` events arrive (`layer.py:213-220`). Out-of-order arrival across the network is handled by index keying (same argument as §2.1).
- **What is NOT claimed:** cross-node PIT replication, consensus, or a distributed Merkle root. If E wants to *demonstrate* aggregation across nodes, it does so by having multiple remote producers answer leaves of one parent held at the intake — which is exactly the fan-out.

### 3.2 Provisioning / runbook structure (structure only)

No Ansible artifacts exist in-repo; this is greenfield. Recommended structure (contents are design, not scripts):

```
deploy/pi/
  inventory.ini            # [intake] [edges] [producers] [llm] [observer] host groups
  group_vars/
    all.yml                # PYTHON_VERSION, PICN_REPO, PICN_COMMIT, VENV_PATH
    edges.yml              # edge listen ports, capability descriptors, backend kind
    intake.yml             # intake seed list, k list, leaf_latency_s, out_dir
    llm.yml                # model preference TOML path, ollama endpoint (E-LLM only)
  host_vars/
    pi-01.yml … pi-08.yml  # per-node: role, IP, clock offset measurement method
  roles/
    picn_base/             # install python3.14, clone repo at PICN_COMMIT, pip install -e ".[dev]"
    picn_node/             # template a node config (role, ports, peers) and run the forwarder under systemd
    picn_measure/          # run the intake runner, collect JSONL to the observer
  runbooks/
    bringup.md             # exact ordered commands
    teardown.md
    clock_sync.md          # chrony/NTP + measured residual skew
```

**Host inventory format (design contract):**
```ini
[intake]
pi-01 ansible_host=10.0.0.11 role=intake

[edges]
pi-02 ansible_host=10.0.0.12 role=edge edge_port=9001
pi-03 ansible_host=10.0.0.13 role=edge edge_port=9002

[producers]
pi-04 ansible_host=10.0.0.14 role=producer capability="hospital/beds"
pi-05 ansible_host=10.0.0.15 role=producer capability="hospital/beds"

[llm]
pi-06 ansible_host=10.0.0.16 role=llm model_endpoint="http://127.0.0.1:11434"

[observer]
pi-07 ansible_host=10.0.0.17 role=observer

[all:vars]
picn_commit=<sha>
python_version=3.14
```

**Env / config contract (per node):**
- `PICN_ROLE` = `intake|edge|producer|llm|observer`
- `PICN_LISTEN_PORT`, `PICN_PEER_HOST`, `PICN_PEER_PORT`
- `PICN_BACKEND` = `deterministic|llm`
- `PICN_MODEL_CONFIG` = path to the TOML (only when `PICN_BACKEND=llm`)
- `PICN_RESULTS_DIR`, `PICN_RUN_ID`, `PICN_SEED`, `PICN_K`, `PICN_LEAF_LATENCY_S`

**Exact bring-up commands (structure):**
```bash
# 0. clock sync + skew measurement on every node
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml --tags base,clock

# 1. deploy nodes (install repo at PICN_COMMIT, template node configs, start systemd units)
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml --tags deploy

# 2. health check (each edge answers a known capability Interest)
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml --tags healthcheck

# 3. run one measured campaign from the intake
ssh pi-01 'cd $PICN_REPO && $VENV/bin/python -m demo.run_physical \
  --seed 1 --k 8 --backend deterministic --leaf-latency-s 0.05 \
  --run-id e1-det-$(date +%s) --out-dir $PICN_RESULTS_DIR'

# 4. collect JSONL to observer
ansible-playbook -i deploy/pi/inventory.ini deploy/pi/playbook.yml --tags collect
```

### 3.3 Deterministic-vs-LLM toggle

**CLI: `demo/run_physical.py`**
```bash
python -m demo.run_physical \
  --backend {deterministic,llm} \
  --seeds 1-5 --k 8 \
  --leaf-latency-s 0.0 \
  --model-config deploy/pi/group_vars/llm/model.toml \
  --transport udp \
  --run-id <id> \
  --out demo/results/physical/<run-id>.jsonl
```

**TOML model-config contract** (consumed by `load_model_preference`, `pydantic_ai_backend.py:32-55`; local-first):
```toml
[model]
preference = ["ollama:llama3.2", "openai:gpt-4o-mini"]
```
- `--backend deterministic` never loads this file; producers register `DeterministicBackend` (optionally wrapped in `LatencyBackend` for controlled transport-independent delay).
- `--backend llm` constructs `PydanticAIBackend(input_model=…, output_model=…, model_config_path=--model-config)` at producers. **This import stays inside `agentic/binding/pydantic_ai_backend.py` and is referenced only from `demo/`**, preserving AC3 (`test_architecture.py:266-275`). The `demo/` runner may import it because `demo/` is not in `FORWARDING_PATH_PACKAGES`.

**Per-leaf latency becomes LLM-inference dominated — honestly stated:**
- In `--backend llm`, the dominant per-leaf latency is model inference (Ollama warm/cold, prompt/response size), **not** the network. `LatencyBackend` is **not** used to fake it; the measured end-to-end per-leaf time is reported as observed.
- E must record, per leaf: `inference_ms` (measured around `backend.invoke` at the producer), `transport_ms` (producer→intake round trip minus inference, derived from event timestamps), and `total_ms`. **The report must show the split** and must not attribute inference time to the ICN overlay.
- Cold-start inference (first call loads the model) is a known outlier and goes through the **same trimming policy as SimulationBus cold start** (§3.5) — but trimming rules and counts must be disclosed.

### 3.4 Honest framing discipline — separate claims, gates, namespaces

**D and E are different claims and must never share a number:**

| | Experiment D | Experiment E |
|---|---|---|
| Transport label | `bus` (SimulationBus) | `udp` (physical) |
| Nature | contained simulation illustration | real deployment |
| Backend | `DeterministicBackend` only | `DeterministicBackend` (+ optional `PydanticAIBackend`) |
| Gate | `concurrent_publishable` | `physical_publishable` |
| JSONL namespace | `demo/results/concurrency.jsonl` | `demo/results/physical/<run-id>.jsonl` |
| Kind tag | `kind="concurrency_run"` | `kind="physical_run"` |
| Report section | "SimulationBus fan-out illustration" | "Physical deployment (8 Pi 5, UDP)" |

**`physical_publishable` gate — exactly when True:**
> `physical_publishable` is `True` **iff**:
> 1. `metadata.transport == "udp"` and the run executed on ≥ 8 distinct physical hosts (recorded host inventory + per-node run markers).
> 2. `metadata.reproducible is True` (clean tree) **or** `metadata.parameters["deployment"]` records the exact `PICN_COMMIT` and `--run-id`.
> 3. The intake's `AgenticLayer` `submit_intent` completed with `trace_root_verified is True`.
> 4. Per-cell run count ≥ `MIN_PHYSICAL_RUNS` (e.g. 5) **and** the same `(seed, k, backend, leaf_latency_s)` was used across runs; mean ± stdev reported.
> 5. If `backend="llm"`, the latency split (`inference_ms` vs `transport_ms`) is present for every leaf; otherwise the run cannot claim to measure the overlay.
> 6. Host clock skew across nodes, as measured before the run, is recorded and below the declared threshold; runs above threshold are excluded from the publishable set (and reported as excluded).
>
> Failing any condition → gate `False`, figures refuse to render, and the record is retained for audit with `physical_publishable=False`.

**Caption/claim rules (mandatory):**
- Every D caption contains: *"SimulationBus illustration on a single host; deterministic backends; not a deployment measurement."*
- Every E caption contains: *"Physical deployment on N Raspberry Pi 5 nodes over UDP at commit `<sha>`; measured wall-clock includes transport and (for the LLM variant) model inference, reported separately."*
- **Forbidden:** citing a D speedup in an E claim, or vice versa; describing D as a "deployment"; describing E as "controlled" or "comparable to D"; collapsing D+E into one number.
- **Namespace enforcement:** the D aggregation module reads only `kind="concurrency_run"`; E's report generator reads only `kind="physical_run"`. A shared summary must keep them in separate top-level keys, never averaged.

### 3.5 Statistical discipline for real UDP

- **Runs per cell:** ≥ 5 (default), configurable `--runs-per-cell`. Cells are `(seed? or repetition, k, backend, leaf_latency_s)`.
- **Jitter handling vs SimulationBus cold-start trimming:**
  - SimulationBus uses `_trim_cold_start(cap_ms=500.0)` (`generate_commag_report.py:42-45`) to drop multi-second stalls.
  - **Real UDP must NOT use the same blind cap.** Rationale: UDP jitter is a *measurement of interest*, not an artifact to trim. Policy:
    1. Report the **full sample** mean ± stdev and median (primary).
    2. Report a **trimmed** variant only if a documented outlier rule fires (e.g. > 3×IQR or a cold-start first-run), with the **count trimmed disclosed**.
    3. Cold-start inference (LLM variant, first call per producer) is identified by a per-producer warm flag and excluded from primary stats, with exclusion count disclosed.
  - The report must show both, or state explicitly that no trimming was applied.
- **Mean ± stdev mandatory** on every table (matching existing discipline in `generate_commag_report.py` and `run_cardiac_bus.py`).
- **Clock discipline:** all inter-node intervals must be measured with a single clock domain per node; cross-node intervals are derived from node-local monotonic clocks plus measured offset, or avoided (prefer intake-side monotonic timing around `submit_intent`, which is single-clock). Record residual skew with the results.
- **Environment recorded:** kernel, Python version, `PICN_COMMIT`, CPU governor, thermal state if obtainable, network medium (switch vs direct), and whether nodes were idle.

### 3.6 File plan (Experiment E)

| File | Action | Purpose |
|---|---|---|
| `demo/run_physical.py` | **new** | E runner CLI: `--backend`, `--transport udp`, `--model-config`, `--runs-per-cell`; writes `kind="physical_run"` records with latency split |
| `demo/physical_topology.py` | **new** | E-only wiring: intake `AgenticLayer` + `PicnSubstratePort(peer_host, peer_port)`; producers with `DeterministicBackend`/`LatencyBackend`/`PydanticAIBackend`; role selection from env |
| `demo/physical_node.py` | **new** | Per-node entrypoint (edge/producer/llm/observer) selected by `PICN_ROLE`; systemd-friendly |
| `agentic/benchmark/physical.py` | **new** | E snapshot + `physical_publishable` gate (pure Python, **no `PiCN.*` import** → AC1 safe; it processes records, not the stack) |
| `deploy/pi/inventory.ini` | **new** | host inventory (structure in §3.2) |
| `deploy/pi/playbook.yml` + `deploy/pi/roles/**` + `deploy/pi/group_vars/**` + `deploy/pi/host_vars/**` | **new** | provisioning structure |
| `deploy/pi/runbooks/bringup.md`, `teardown.md`, `clock_sync.md` | **new** | exact bring-up commands + clock policy |
| `deploy/pi/group_vars/llm/model.toml` | **new** | model preference contract (local-first) |
| `demo/generate_physical_report.py` | **new** | E report + captions; reads only `kind="physical_run"` |
| `agentic/tests/test_physical_gate.py` | **new** | `physical_publishable` true/false conditions; refuses `<8` hosts, unverified root, missing split |
| `agentic/tests/test_run_physical_cli.py` | **new** | CLI contract (backend toggle, model config path, namespace isolation) |
| `demo/README.md`, `docs/agentic_demo.md` | modify | document E, distinguish from D |

**AC compatibility:** `demo/` and `deploy/` are outside the `agentic/` import contracts (`test_architecture.py` scans `agentic/` only). `agentic/benchmark/physical.py` must remain pure (no `PiCN.*`, no LLM client) to satisfy AC1/AC3.

---

## 4. Metric schema evolution (exact, backward-compatible)

### 4.1 `MetricEvent` — no structural change

`MetricEvent(kind, transport, seed, value, labels)` (`events.py:29-47`) is **frozen as-is**. No new required fields. Rationale: adding a required field would break every constructor call in existing tests and runners. All new information is carried in `labels`, which is already `dict[str, Any] | None`.

### 4.2 `EventKind` — additive only

Append (never remove/reorder) to the `Literal` (`events.py:14-26`):
```
"fanout_serial_ms",
"fanout_concurrent_ms",
"fanout_speedup",
"leaf_inflight_peak",
"leaf_overlap_fraction",
"leaf_start_ms",
"leaf_end_ms",
"config_snapshot",        # one per physical/DD run: full config as labels
```
Existing kinds (`latency_sample`, `message`, `nfn_baseline_latency`, `agentic_latency`, `detection`, `scale_point`, `context_pit_peak`, `context_pit_entries`, `dispatch_count`, `aggregation_complete`, `artefact_bytes`) are untouched. `Literal` widening is backward-compatible for producers; consumers that use `if kind == …` are unaffected.

### 4.3 `MetricsSnapshot` — additive optional fields

`MetricsSnapshot` (`metrics.py:31-60`) gains **optional** fields with `None` defaults (mirroring how `context_pit_peak`, `dispatch_count`, etc. were added):
- `concurrent_publishable: bool | None = None`
- `fanout_serial_ms: float | None = None`
- `fanout_concurrent_ms: float | None = None`
- `fanout_speedup: float | None = None`
- `leaf_inflight_peak: float | None = None`
- `leaf_overlap_fraction: float | None = None`
- `physical_publishable: bool | None = None`

Paper-facing aliases (mirroring `nfn_stack_overhead_*`, `metrics.py:62-70`):
- `concurrent_dispatch_publishable` → `concurrent_publishable`
- `physical_deployment_publishable` → `physical_publishable`

`compute_metrics` (`metrics.py:73-132`) gains **no new computation** in its hot path for D/E beyond reading the new events into the optional fields (a short additive block at the end, guarded by `if any(e.kind == …)`), so existing outputs for existing event sets are **byte-identical** when the new kinds are absent.

### 4.4 Coexistence table

| Metric family | Gate | Transport | Populated by | Status |
|---|---|---|---|---|
| `m1_latency_ms` | none (report absolute only from UDP) | bus/udp | harness | unchanged |
| `m3_overhead_ratio` / `nfn_stack_overhead_ratio` | `m3_publishable` / `nfn_stack_overhead_publishable` | bus | `run_paired_bus` | **unchanged, untouched by D/E** |
| `concurrent_publishable` | new | bus | D (`run_concurrency`) | new, independent |
| `physical_publishable` | new | udp | E (`run_physical`) | new, independent |

The three gates are **independent booleans**. No gate implies another. A record can be `m3_publishable=True, concurrent_publishable=False` and vice versa. Report generators must check the gate that matches their figure.

---

## 5. Honesty & anti-strawman checklist

**Experiment D:**
- [ ] Serial baseline is the **actual** current code path (`submit_intent` serial), not a synthetic slow loop.
- [ ] Serial and concurrent runs use the **same** leaf set, same `k`, same `leaf_latency_s`, same backend, same seed.
- [ ] `leaf_latency_s > 0` for any published speedup; zero-latency runs are explicitly non-publishable.
- [ ] Trace roots are asserted byte-identical between modes (proves accountability is not traded for speed).
- [ ] Speedup is **never** computed across different sweeps or different leaf sets.
- [ ] SimulationBus framing is on every caption; no deployment language.
- [ ] I2 is shown intact: a test verifies commit precedes all sends under concurrency (e.g. observer records commit index before first `RequestSent`).
- [ ] Out-of-order completion is exercised (not just claimed) with `hold_terminals` + reversed `release_terminals`, and the resulting root is verified.
- [ ] Peak in-flight and overlap are **measured**, not asserted from theory.
- [ ] Any flattening of speedup at large `k` is reported, not hidden.

**Experiment E:**
- [ ] ≥ 8 distinct physical hosts, recorded by inventory and per-node markers.
- [ ] `transport=udp` in metadata; no SimulationBus anywhere in an E run.
- [ ] Latency split reported for LLM variant (inference vs transport); overlay is not credited with inference time.
- [ ] Context PIT is stated as **intake-local**, not distributed.
- [ ] Clock skew measured, reported, and thresholded; excluded runs are counted.
- [ ] `MIN_PHYSICAL_RUNS` met; mean ± stdev reported on full sample; any trimming disclosed with counts.
- [ ] Cold-start inference handling disclosed.
- [ ] `physical_publishable` gate enforced before any E figure renders.
- [ ] D and E numbers never appear in the same table/figure/derived ratio.
- [ ] Caption rule text present verbatim.

**Cross-cutting anti-strawman:**
- [ ] D does not claim to beat Temporal/Step Functions; it measures the *ICN mechanism* (fan-out + accountability). A/B handle the production-system comparison.
- [ ] E does not claim quantitative superiority over D or any orchestrator; it is a feasibility deployment.
- [ ] No "we beat X" language; language stays "the walkthrough demonstrates / the analysis suggests" (per `EXPERIMENT_PLAN.md:132-134`).
- [ ] All gates documented in `EXPERIMENT_PLAN.md` and `demo/README.md`.

---

## 6. Risks & open questions for Dennis

**Risks (design-level, to be resolved before/while implementing):**

1. **Port demux ambiguity under high concurrency (HIGH).** `PicnSubstratePort._match_outstanding` (`adapters/picn/port.py:262-269`) does a linear scan and matches on **prefix in either direction**. Two outstanding Interests whose names are prefixes of each other (e.g. `/cap/fwd/hospital/beds/h1` vs `/cap/fwd/hospital/beds/h10`) could return the **wrong correlation**, corrupting the Merkle leaf. D's `k∈{2,3,4,5,8}` does not hit `h10`, but E at scale, or any future naming change, can. **Recommendation:** before E, either (a) make correlations name-unique and match by exact name, or (b) add a per-run naming scheme that guarantees non-prefix names. This is a **correctness bug surfaced by D/E**, not just a measurement issue. Needs a decision.

2. **`TaskGroup` vs `ExceptionGroup` on the agentic path (MEDIUM).** `AsyncLayerProcess.run` deliberately avoids `TaskGroup` to keep exceptions unwrapped (`AsyncLayerProcess.py:98-107`). D's `submit_intent` will use it. If any port implementation violates "never raise across the boundary," a leaf error becomes an `ExceptionGroup`. Mitigation designed (unwrap + re-raise), but needs a test and a documented rule in the ADR style.

3. **`DEFAULT_INBOUND_SIZE=64` and `MAX_CONTEXT_PIT_ENTRIES=256` are PROVISIONAL (MEDIUM).** Concurrent fan-out increases the in-flight event rate into the bounded inbound queue (`layer.py:47`) and the port's `_put` awaits on a full queue (`port.py:319`) — a full queue back-pressures the adapter and could serialize what D is trying to measure. At `k=8` this is fine; at larger `k` it may cap speedup for reasons unrelated to ICN. D must report queue-bound effects and E must confirm defaults on hardware.

4. **LatencyBackend placement conflates inference with transport if done wrong (MEDIUM).** D measures *backend* latency at the producer; if the wrapper is instead placed on the port, the number changes meaning. Design pins it to the producer backend and documents it; a test must assert the wrapper is invoked inside `hub.invoke` (producer path, `layer.py:198`), not in `send_request`.

5. **`PicnSubstratePort.send_request` registers `_outstanding` before `await` (`port.py:187`) but does not bind the `payload` (`port.py:210` `_ = payload`).** For UDP this is fine today, but D/E should note that Interest parameters are not yet transmitted; a future change alters the wire behaviour and must be handled separately (AGENTS.md rule).

6. **Clock domain mismatch for E (MEDIUM).** `PicnSubstratePort` uses `time.monotonic` by default (`port.py:75`); node clocks are independent. Intake-side timing around `submit_intent` is single-clock and safe; cross-node per-leaf split requires offset correction and honest error bars. Flagged.

7. **No regression guarantee on existing sweeps (LOW, but must be verified).** The `submit_intent` change must default to `dispatch="serial"` and produce identical events/roots for existing callers (`demo/cardiac_bus_topology.py`, tests). A golden test re-running the existing cardiac bus and comparing the trace root and dispatch count is required.

**Open questions for Dennis:**

1. **Port demux fix scope:** do we fix the prefix-match ambiguity as part of D/E (recommended, since E will likely trip it), or file it separately and constrain names to `h0…h7`? This determines whether D/E are purely additive or touch the adapter.
2. **`LatencyBackend` home:** `agentic/binding/latency.py` (my recommendation — backend-shaped, protocol-conformant, reusable by E) vs a demo-local class. Confirm it belongs in the library, since it changes `binding/` public surface.
3. **D's latency source:** producer-side `LatencyBackend` (recommended, matches "capability service latency") vs an explicit `LatencyPort`. Confirm the claim we intend to make.
4. **E's first configuration:** single edge + k producers (simplest) vs true multi-edge fan-out. This affects topology, provisioning, and whether E demonstrates name-based *routing* across nodes or just fan-out to producers on one node.
5. **`MIN_PHYSICAL_RUNS` and the E publishability threshold:** 5 runs is a proposal; is there a target for a venue (TNSM) that wants more?
6. **LLM model selection for E-LLM:** `ollama:llama3.2` on a Pi 5 or a dedicated GPU host? Pi 5 CPU inference will dominate and may make the overlay's contribution unmeasurable — do we want the LLM on a separate more capable node, or keep it on-Pi for honesty about edge constraints?
7. **Zero-latency determinism:** should D also run `leaf_latency_s=0` cells to show that **even a correct concurrent implementation yields ~1.0× speedup with instant backends** (the honest baseline that the current code isn't "wrong," just unmeasured)? Recommended: yes, as a control cell.
8. **Where D/E live in the paper:** same evaluation section (two fidelity levels) or separate (D with A/B/C, E as a standalone deployment section)? Affects report structure and captions.

---

*End of design. No implementation code written. Every claim about existing code cites a verified file:line. Items I could not verify from the code are explicitly flagged in §0 and §6.*
</task_result>
</task>
---

# DECISIONS LOG (Dennis, 2026-09-12) — design revisions required

## Decision 1 — Port demux / name correlation (was Open Q1)
**Chosen: Option 2.** Replace bidirectional-prefix scan in `PicnSubstratePort._match_outstanding`
with **true directional longest-prefix matching** (the NFN-paper definition), PLUS bind a
**correlation token to the outgoing Interest and require it echoed in the Content**.
Matching = exact correlation when present, else LPM fallback. This makes it wildcard- and
aggregation-ready (token is name-independent). New **ADR `A-012-name-correlation-and-wildcards.md`**,
own commit, framed as a protocol feature (not a refactor), citing the NFN LPM definition.
Full wildcard-resolver table reclassified as future work.

## Decision 2 — LatencyBackend home (was Open Q2)
**Chosen: Option A.** `agentic/binding/latency.py`, first-class generic wrapper, exported from
`agentic/binding/__init__.py`. Off by default. `jitter_s` param enables artificial "slowing" when
a scenario demands it. `jitter_s=0` for D (deterministic).

## Decision 3 — Primary metric = end-to-end T_intent with decomposition (REVISES Open Q3)
Dennis requires: **measure how long an intent takes from emission to verified/aggregated output,
and isolate what the agentic/ICN substrate adds.**

**T_intent = T_decompose + T_dispatch + T_network + T_service + T_aggregate**

- `LatencyBackend` (producer-side) is a **controlled service-time sweep axis**
  (`leaf_latency_s ∈ {~0, 10ms, 50ms, 100ms, 500ms}` configurable) — answers "when does the
  network substrate matter relative to service latency?"
- **New `LatencyObserver`** (extends the proposed `DispatchObserver`): per-leaf timestamps
  `t_send`, `t_response`, `t_service`, computed `t_network = t_response - t_send`.
- Reused by BOTH D and E so the decomposition is comparable across substrates.
- **Honesty traps to encode in captions:**
  - D (SimulationBus): T_network is near-zero by construction (in-process bus). D's honest
    claim = dispatch/aggregate/decomposition overhead isolated; transport not meaningful.
  - E (real UDP on Pis): T_network is real and measurable; report its actual share.
  - Co-located vs distributed producers: D = intake-side timestamps only (single clock, clean).
    E = add producer-side timestamps with measured clock-offset correction.

**Sub-decisions confirmed:**
1. Service-latency sweep axis: YES (`{~0, 10ms, 50ms, 100ms, 500ms}`, configurable).
2. T_network timestamping: D = intake-port only (single clock). E = intake + producer, offset-corrected.

**Action:** DESIREE to revise D & E design v2 incorporating the T_intent decomposition,
LatencyObserver, service-latency sweep, and the two honesty traps.

## Decision 4 — E topology (was Open Q4)
**Chosen: Option B, staged.** Build the physical runner with `--edges N` (N>=1).
First publishable campaign uses **N=2 edges**, k=8 split across them, so the paper demonstrates
**capability-name-based routing across physical nodes** (LPM over `/cap/fwd/hospital/beds/*`
in the FIB), not just fan-out to one node. N=1 remains a supported control cell.
Intake holds one `PicnSubstratePort` per edge; hospital capability prefixes map to the owning edge.
This exercises Decision 1 (true LPM + correlation token) in a real routing scenario.

## Decision 5 — E run counts / publishability threshold (was Open Q5)
**Chosen: cell-aware, strong floor.** `MIN_PHYSICAL_RUNS >= 30` for ALL E cells
(deterministic AND LLM), configurable via `--runs-per-cell`. `physical_publishable` requires the
cell's threshold. Always report n / median / mean / stdev; disclose exclusions (cold start,
thermal). "The more the better" — 30 is the floor, not the ceiling.

## Decision 6 — LLM placement for E-LLM variant (was Open Q6)
**Chosen: Option D, staged.** Ship **A first** (on-Pi Ollama per producer), then add **C**
(off-Pi GPU host, e.g. DGX Spark on LAN) as a second sub-variant. AVOID B (dedicated on-LAN LLM
node) because LLM RPC traffic would conflate T_network with T_service.
Paper story: substrate contribution is X% of T_intent at edge-realistic on-Pi latency, and
shrinks further with larger off-board models -> network cost is bounded and predictable.

## Decision 7 — Zero-latency control cells in D (was Open Q7)
**Chosen: YES.** Run `leaf_latency_s = 0` control cells in every D campaign (expected ~1.0x
speedup). Proves the speedup comes from real overlap, not measurement machinery; pre-empts the
"artifact" reviewer attack.

## Decision 8 — D/E placement in the paper (was Open Q8)
**Chosen: Option A.** One evaluation section, TWO clearly-labelled fidelity tiers:
D = SimulationBus mechanism illustration; E = physical deployment (8+ Pi 5, UDP).
HARD RULE: D and E numbers never share a table, figure, or derived ratio.
Directly answers R4 "no PoC" with "two PoCs at different fidelity levels."

---
# ALL 8 OPEN QUESTIONS RESOLVED. DESIREE v2 revision to incorporate Decisions 1-8.
