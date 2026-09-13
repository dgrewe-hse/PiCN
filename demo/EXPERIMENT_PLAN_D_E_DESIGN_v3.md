# DESIGN v3 — Experiments D & E: Concurrent Fan-Out, T_intent Decomposition, and the Physical Testbed for Agentic Routing

**Author:** DESIREE 🏗️ (architecture & design)
**Status:** DESIGN v3 (supersedes v2; not implemented)
**Repo:** `gh-repositories/PiCN` (fork of Basel PiCN)
**Target:** revise COMMAG-26-00578 (rejected) + TNSM/Computer Networks reframing
**Date:** 2026-09-12
**Incorporates:** Decisions 1–8 (v1/v2), Dennis' Option C producer-path decision, Dennis' Option 3 (staged) A-012 decision, the ARCO BLOCK verdict (12 findings), and the mandatory wire-format transparency note.
**Supersedes:** `EXPERIMENT_PLAN_D_E_DESIGN.md` (v1), `EXPERIMENT_PLAN_D_E_DESIGN_v2.md` (v2).

**v3 headline:** v2 measured a topology the code does not have. v3 first **builds the producer path as its own milestone (PR-1)**, then measures it. Every gate is re-specified so it cannot be satisfied by a topology that does not exercise the mechanism.

---

## 0. Verification notes (what I read this revision, what I could NOT verify)

**Re-verified against code for v3 (file:line):**

- **Producer path is dead (ARCO BLOCKER 1 — CONFIRMED, and the fix point is DIFFERENT from the ARCO note).** `AgenticLayer._on_inbound_request` (`layer.py:179-211`) is the only caller of `hub.invoke` (`layer.py:198`). It is reached only via `InboundRequest`, which is produced only by `PicnSubstratePort.inject_interest` (`port.py:215-226`) and `MockSubstratePort.inject_inbound_request` (`mock/port.py:138-159`). **Neither has a production caller.** The ARCO note says the fix belongs in `PicnSubstratePort._deliver_uplink_packet`; that is **incorrect**: `_deliver_uplink_packet` (`port.py:271-311`) is the **client-side uplink demux** of Content/Nack *arriving at the requester*. The producer node receives Interests through its **layer stack**, and `AgenticForwarder` assembles `AgenticLayer` above `AsyncBasicICNLayer` (`AgenticForwarder.py:120-152`). With `self.icnlayer._interest_to_app = True` (`AgenticForwarder.py:124`), an Interest with no CS/CS-miss and no matching FIB reaches `Outbound("queue_higher", [face_id, interest])` (`ICNLayerCore.py:120-124`), i.e. it is pushed up the stack to the layer above — which is `AgenticLayer.data_from_lower`. That method is a **no-op today** (`layer.py:315-328`). **Therefore PR-1's producer-side translation point is `AgenticLayer.data_from_lower`, not the adapter.** This is a material correction to the Option C text and it is verified, not guessed.
- **`send_response` never transmits (ARCO BLOCKER 2 — CONFIRMED).** `PicnSubstratePort.send_response` only writes `self._response_payloads[correlation] = payload` (`port.py:212-213`). No `Content`, no enqueue. Class comment confirms "AgenticForwarder wiring lands in G.3" (`port.py:53-54`). **Additionally:** even on the producer node, `AgenticLayer._on_inbound_request` calls `self._port.send_response(...)` (`layer.py:208-211`) — so the producer must have a `SubstratePort` attached, and that port's `send_response` must build a `Content` and inject it **down through the producer's own layer stack** so the ICN/FIB/PIT path returns it to the requester's face. `PicnSubstratePort`'s stack (`port.py:81-98`) is the *client* stack (chunk/timeout/packetencoding/link) and has no ICN layer; returning Content through it would put a packet on the wire without the producer's PIT resolving it. PR-1 must therefore give the producer node a **forwarder-side** response path.
- **`_match_outstanding` is bidirectional (ARCO MAJOR 4/5 — CONFIRMED).** `port.py:262-269`: forward clause `interest_name == content_name or interest_name.is_prefix_of(content_name)` at line 265, **reverse clause `content_name.is_prefix_of(interest_name)` at line 267**. `longest_prefix_match` legitimately contains `prefix.is_prefix_of(name)` at `port.py:172`. So a grep for `is_prefix_of` cannot distinguish the defect; v3 verifies **absence of `content_name.is_prefix_of`**.
- **Late/duplicate response crashes (ARCO MAJOR 5 — CONFIRMED).** `_on_response` (`layer.py:213-220`) does not check `entry.terminated`; `ContextPIT.record_response` raises `ValueError("entry already terminated")` (`context_pit.py:202-204`). `_on_failed` (`layer.py:230-237`) has the same exposure. Nack demux picks `next(iter(self._outstanding.items()))` — dict insertion order, arbitrary under concurrency (`port.py:296-311`).
- **TaskGroup hang (ARCO MAJOR 6 — CONFIRMED structurally).** v2's `TaskGroup` design (`design v2 §2.1`) has no failure mapping. If any leaf task raises or is cancelled by a sibling error, no `_on_failed` is emitted, `done` never resolves, and `await done` (`layer.py:313`) hangs until the caller's `asyncio.wait_for`.
- **`submit_intent` serial (CONFIRMED).** `layer.py:295-313`. Commit at `layer.py:284-290` precedes the loop; commit is synchronous. Index-keyed Merkle via `record_response(parent, leaf_index, …)` (`layer.py:219`) and `entry.tree.set_leaf(leaf_index, …)` (`context_pit.py:207`). `maybe_complete`/`terminate` idempotent (`aggregation.py:146-147`; `context_pit.py:225-226`); `done` guarded (`layer.py:244`).
- **`Interceptor`/producer-side backend invocation** happens only at `layer.py:198-200`.
- **Wire reality (CONFIRMED).** `Interest(name, wire_format)` — no parameter field (`Interest.py:10-16`). `Content(name, content, wire_format)` (`Content.py:10-18`). `NdnTlvEncoder.encode_interest` writes Nonce+Name only (`NdnTlvEncoder.py:149-166`); `decode_interest` returns only the Name (`NdnTlvEncoder.py:271-279`). No parameter carries the token.
- **`Transport` metadata closed literal; EventKind closed literal** (`events.py:14-26`); `labels: dict[str, Any] | None` is the additive channel.
- **AC1** forbids `agentic.benchmark` importing `PiCN.*`; **AC2** constrains `layer.py` imports; **AC3** forbids LLM clients in the forwarding-path packages (per `AGENTS.md` and v1 research).
- **`AgenticForwarder.register_capability` exists** (`AgenticForwarder.py:169-179`) but the cardiac demo **never calls it**; instead `_configure_edge_cs` (`cardiac_bus_topology.py:150-166`) installs hospital Content directly into the edge Content Store via `MgmtClient.add_new_content`. That is why `hub.invoke` is never reached.
- **`CapabilityProducerHub.invoke` → registry → backend** (`producer.py:82-93`); registry resolves the descriptor (`producer.py:74-77`).

**Could NOT verify (flagged, no guessing):**
- Whether the `AsyncLayerStack` delivers `queue_higher` items to the correct layer's `data_from_lower` with the face id preserved. The stack contract (`AsyncBasicICNLayer.data_from_lower` → `_apply_outbound` `queue_higher` → `self.queue_to_higher.put` → next layer up `data_from_lower`) is read from code, but the **end-to-end delivery to a non-no-op `AgenticLayer.data_from_lower`, including the face id needed to reply, is the first thing PR-1 must prove with a live test.** Flagged.
- Whether `AsyncBasicICNLayer` can accept a `Content` injected from the app (`data_from_higher`) and route it back along the PIT face. `handle_content` (`ICNLayerCore.py:142-157`) requires a PIT entry; the producer's PIT entry exists only if the incoming Interest was recorded there. PR-1 must confirm the producer records the requester face in its PIT on the `_interest_to_app` path (`ICNLayerCore.py:120-124` does `pit.add_pit_entry(... face_id ... local_app=from_local)`), and then `handle_content` returns it (`ICNLayerCore.py:150-155`). Read, but **unproven by test.** Flagged.
- Whether `Content` can be pushed from `AgenticLayer` down via `data_from_higher` with a `[face_id, content]` shape that `handle_from_higher` accepts. Not read in full. PR-1 investigation item.
- No `deploy/`, inventory, or Pi artifacts exist in-repo. E provisioning is greenfield.
- No `LatencyBackend`, no `LatencyObserver`, no `runs-per-cell` UDP policy exist. New.
- `MAX_CONTEXT_PIT_ENTRIES=256` (`context_pit.py:28`) and `DEFAULT_INBOUND_SIZE=64` (`layer.py:47`) are PROVISIONAL.

**Corrections to prior art (recorded so CRAFT does not implement the wrong fix):**
1. The producer-side Interest→`InboundRequest` translation belongs in **`AgenticLayer.data_from_lower`** (the layer), not in `PicnSubstratePort._deliver_uplink_packet` (the client adapter). The adapter's uplink path demuxes *responses*.
2. The producer-side `send_response` needs a path into the **forwarder's** ICN layer (so the PIT face resolves and Content is written back), not merely "enqueue down the adapter's client stack."

These two corrections are the core of PR-1 §4 below.

---

## 1. Goal & reviewer mapping (intent unchanged)

### 1.1 The two skeptical-committee objections this must kill

> **(a)** "The strongest ICN win is untested. Everything is a single in-flight intent on a shared bus. The actual benefit of name/fan-out in an ICN/agentic overlay is untested."
>
> **(b)** "What does your solution add? Temporal / Step Functions / SPIFFE / OTel / MCP already orchestrate fan-out and time it."

D and E jointly attack (a). The **T_intent decomposition** attacks (b) by reporting **what share of an intent's end-to-end time each substrate stage contributes**, so the reviewer can see what the agentic/ICN layer adds and whether that share is bounded. A single wall-clock number cannot answer (b); a decomposition can. **v3 makes the decomposition non-vacuous by making `T_service` and `T_network` genuinely measurable (PR-1 + D's real producer path).**

### 1.2 Reviewer complaint → experiment mapping

| Complaint | Source | Answered by | How |
|---|---|---|---|
| R1 #6 / #7 — weak comparison vs Temporal / Step Functions / SPIFFE / OTel / MCP; **"what does Agentic Routing add?"** | R1 | **D + E via T_intent decomposition** | `T_intent` decomposed into stages; the substrate's share is reported numerically. v3 guarantees `T_service` is non-zero and measured (real producer backend), and `T_network` is real in E. |
| Committee concurrency objection — single in-flight intent, fan-out benefit untested | committee | **D (primary), E (secondary)** | D produces a **speedup-vs-k curve** on the exact leaf set with a paired serial baseline, up to k=8, over a **real producer path** (PR-1). E repeats over real UDP. |
| R1 #3 / #4 — Context PIT omission-accountability / attestation limits underspecified | R1 | **D (partial), E (partial)** | D stress-tests omission accounting under out-of-order completion at k up to 8; E across real nodes. Deeper attestation remains C's remit. |
| R3 #3 / R4 — no implementation / simulation / PoC | R3, R4 | **D AND E** | Two PoCs at different fidelity tiers. |
| R4 — emergency scenario too high-level | R4 | **E** | Physical multi-node deployment with named roles; capability-name routing across ≥2 physical edges. |
| "Your speedup is a measurement artifact" | anticipated | **D zero-latency control cell (fixed gate)** | `leaf_latency_s=0` cells must yield ~1.0× and are **structurally bound** to `control=true` (Finding 8a); they can never satisfy a speedup claim. |

### 1.3 Non-goals (explicit)

- D does **not** claim real network behaviour. Its `T_network` is near-zero by construction; D's honest claim is **dispatch/aggregation/decomposition isolation**. D's `T_service` **is** real once PR-1 lands (the producer backend is actually invoked).
- E does **not** claim a controlled comparison against D. It is a physical deployment measurement.
- Neither touches the NFN combine microbenchmark (`m3` / `nfn_stack_overhead`).
- **The in-band wildcard-correlation token is NOT delivered in this work** (Dennis Option 3). A-012 ships directional LPM now; the token is future work. See §7 and A-012.

---

## 2. Milestone / PR plan and dependency order (NEW — the spine of v3)

Work splits into four sequential PRs. Each is a separate concern (AGENTS.md "one concern per commit"). No PR depends on a later PR.

```
PR-1  G.3 producer wiring ──► PR-2  A-012 demux LPM ──► PR-3  Experiment D ──► PR-4  Experiment E
      (AgenticForwarder/producer   (port.py: delete reverse     (run_concurrency,   (run_physical,
       path actually invokes         clause → directional LPM)    concurrency.py,     physical.py,
       registered backends and                                    observer,           deploy/pi/)
       transmits responses)                                       LatencyBackend)
```

### PR-1 — G.3 producer wiring

**Scope.** Make the registered-producer path functional end-to-end on the async stack: a capability Interest forwarded to a node with a registered capability reaches `AgenticLayer._on_inbound_request` → `hub.invoke`; the response is transmitted back as a `Content` that the requester's port demuxes to the originating correlation. Plus the hardening (idempotent late response, Nack correlation policy, deterministic TaskGroup failure semantics).

**Files:** `agentic/agentic_layer/layer.py`, `agentic/adapters/picn/port.py`, `PiCN/ProgramLibs/AgenticForwarder/AgenticForwarder.py`, tests. Detailed in §3.

**Acceptance criteria (AC-PR1):**
1. A live `SimulationBus` round trip: ambulance `AgenticLayer.submit_intent` → edge `AgenticForwarder` with a **registered** `DeterministicBackend` → backend invoked → `Content` returns → ambulance Context PIT trace root verified. Test asserts `hub.invoke` was actually called (spy/counter), not that a CS preload answered.
2. `send_response` produces a `Content` at the requester.
3. Late/duplicate response after termination is dropped without raising.
4. Nack correlates to the correct outstanding request or is documented + tested as a limitation.
5. TaskGroup failure semantics: any failed/cancelled leaf → `NULL` recorded → `done` resolves. Test.
6. No existing test regresses; `AgenticLayer.data_from_lower` non-no-op path is covered by a new test.

**Unblocks:** PR-3 (`T_service` becomes measurable; `LatencyBackend` at a producer is actually invoked), PR-4 (the UDP return path exists).

### PR-2 — A-012 demux LPM (bug fix only)

**Scope.** Delete the reverse clause `content_name.is_prefix_of(interest_name)` in `_match_outstanding` (`port.py:267`). Keep the forward directional LPM. Make `MockSubstratePort` consistent (it does not use `_match_outstanding`, but its outstanding semantics must be documented as name-independent by correlation). No wire change.

**Files:** `agentic/adapters/picn/port.py`, `agentic/tests/test_name_correlation.py` (new), `Agentic-ICN-Network/agentic_PiCN/decisions/A-012-name-correlation-and-wildcards.md` (placed).

**Acceptance criteria (AC-PR2):**
1. `content_name.is_prefix_of` absent from `agentic/adapters/picn/port.py`.
2. `test_prefix_trap` (h1 vs h10) passes.
3. `test_directional_lpm` passes.
4. Two future-token tests present as `xfail`/`skip` (not falsely green).
5. Existing `test_port.py`, `test_mock_adapter.py` green.

**Unblocks:** PR-3 and PR-4 (both rely on unambiguous demux). Note: PR-3 uses distinct `h*` names anyway, but the fix is required for E's ≥2-edge name routing and for the paper's correctness claim.

### PR-3 — Experiment D

**Scope.** `LatencyBackend`, `LatencyObserver`, `submit_intent` concurrency (TaskGroup) + prepass, `concurrency.py` snapshot/gate, `demo/run_concurrency.py`, `demo/concurrency_topology.py`, report generator, tests. Detailed in §4.

**Acceptance criteria (AC-PR3):** §4.7.

**Unblocks:** PR-4 (observer + LatencyBackend + metric schema reused).

### PR-4 — Experiment E

**Scope.** `demo/run_physical.py`, `demo/physical_topology.py`, `demo/physical_node.py`, `agentic/benchmark/physical.py`, `deploy/pi/**`, `demo/generate_physical_report.py`, tests. Detailed in §5.

**Acceptance criteria (AC-PR4):** §5.7.

### PR ordering rule
PR-1 and PR-2 are independent of each other **only if** PR-2 can merge first; in practice **PR-1 then PR-2** (PR-2 is a one-clause deletion and should not be entangled with PR-1's multi-file wiring). PR-3 requires both. PR-4 requires PR-3 (observer/LatencyBackend reuse). No PR may absorb another's concern.

---

## 3. PR-1 — G.3 producer wiring design (NEW; replaces v2 BLOCKER 1/2)

### 3.1 The topology D will use **after** PR-1

```
                 SimulationBus (async)
  ┌──────────────────────────┐         ┌───────────────────────────────────────────┐
  │ INTAKE / AMBULANCE        │         │ EDGE (AgenticForwarder, runtime=async)      │
  │  AgenticLayer             │         │  AgenticLayer (top of stack)                │
  │   .submit_intent(...)     │         │    register_capability(desc, LatencyBackend │
  │     → serial|concurrent   │         │        (DeterministicBackend(handler)))     │
  │       send_request(name)  │  /cap/fwd/... │  data_from_lower: Interest → Inbound-  │
  │  PicnSubstratePort        │ ───────►│    Request → _on_inbound_request →         │
  │   _outstanding[corr]=name │         │    hub.invoke → backend (sleeps latency_s)  │
  │   _deliver_uplink_packet  │ ◄───────│  send_response → Content down ICN/FIB/PIT   │
  │   ← ResponseArrived        │ Content │                                            │
  │  RecordingLatencyObserver │         │  RecordingLatencyObserver (producer side)   │
  └──────────────────────────┘         └───────────────────────────────────────────┘
```

- **Intake:** `AgenticLayer` + `PicnSubstratePort(edge_addr, None, interfaces=[bus.add_interface(amb_addr)])`, exactly as `cardiac_bus_topology.py:257-264` builds it.
- **Edge:** `AgenticForwarder(port=0, encoder=NdnTlvEncoder(), interfaces=[bus.add_interface(edge_addr)], runtime=Runtime.ASYNC)` (`cardiac_bus_topology.py:210-217`), **but** instead of `_configure_edge_cs` preloading Content into the CS, the edge calls **`edge.register_capability(descriptor, LatencyBackend(DeterministicBackend(handler, …), latency_s=cfg.leaf_latency_s))`** for each capability prefix. This is the change that makes `hub.invoke` reachable.
- **`LatencyBackend` is invoked inside `hub.invoke`** (`layer.py:198-200` → `producer.py:91-93` → registry → `LatencyBackend.invoke`), so `T_service` is real. A test asserts invocation occurred at the producer.

### 3.2 Exact code to build (description only — CRAFT implements)

#### (a) Interest → `InboundRequest` translation — producer side (the dead-code fix)

**Location (corrected):** `AgenticLayer.data_from_lower` (`layer.py:315-328`), replacing the no-op.

- Detect an incoming `Interest` (or the `[face_id, Interest]` outbound shape) arriving from the layer below.
- Preserve the **incoming `face_id`** — it is required to reply. The ICN layer pushes `Outbound("queue_higher", [face_id, interest])` (`ICNLayerCore.py:123`), so the face id travels with the item.
- Construct an `InboundRequest` with: `name` (converted via `from_picn_name`-equivalent for the layer, or a small internal helper — note `layer.py` may not import the adapter; use `PiCN.Packets` name access directly, AC2-permitted), `payload` (empty — Interest carries no payload today; see A-012), a **correlation** (see (d)), `at` timestamp, **and the reply face id** carried on the request context.
- Enqueue onto the layer's existing `self._inbound` event queue (the same queue the port uses) so `_dispatch_event` (`layer.py:167-177`) routes it to `_on_inbound_request`.
- **`InboundRequest` event shape:** the frozen union (`events.py:83-102`) has fields `(correlation, name, payload, at)`. **No face id.** Two options, CRAFT chooses with DESIREE:
  - **Option 1 (preferred):** add an optional `reply_ref: Any = None` field to `InboundRequest`. This is an **agentic-internal** event, not a wire format; widening a dataclass with a defaulted field is backward-compatible for every existing constructor. It does not touch PiCN packet formats. Update the closed-union comment (`events.py:99`).
  - **Option 2:** keep `InboundRequest` frozen and carry the face id in a side table keyed by correlation in `AgenticLayer`. More state, no event change.
  - Either way, `_on_inbound_request` must pass the reply ref to `send_response`.

#### (b) `send_response` builds a `Content` and transmits it

**Location:** the producer node's response path. Two sub-cases:

- **Case A — producer is the `AgenticLayer` atop an `AgenticForwarder`.** `_on_inbound_request` calls `self._port.send_response(...)` (`layer.py:208-211`). On the forwarder, the natural `SubstratePort` is not the client adapter but a **forwarder-side port** that injects a `Content` **down** through the forwarder's own stack so the ICN PIT resolves the requester face. Concretely, PR-1 introduces a forwarder-side response hook (either a small `SubstratePort` implementation backed by the forwarder stack, or `AgenticLayer` gains a `data_to_lower` push). The response path must:
  1. Build `Content(name=<the Interest's name>, content=<payload bytes>)`.
  2. Push it down with the **reply face id** captured in (a), so `AsyncBasicICNLayer.data_from_higher` → `ICNLayerCore.handle_content` (`ICNLayerCore.py:142-157`) finds the PIT entry and emits `Outbound("lower", [face_id, content])`.
  3. The link layer serialises and the requester's `PicnSubstratePort._deliver_uplink_packet` (`port.py:271-294`) demuxes it by name to the correct `correlation`.
- **Case B — producer is behind a client `PicnSubstratePort` (chunk/timeout/packetencoding/link stack, `port.py:81-98`).** Here `send_response` must build a `Content` and enqueue it via `self._lstack.queue_from_higher` on the **link-facing** side. This is the simpler path but requires the probe to have a PIT/FIB that routes back. Case A is the real topology; Case B is the unit-test seam. PR-1 should implement Case A as the production path and keep Case B for `PicnSubstratePort` unit tests.
- **Delete the v2 "no adapter change" claim.** PR-1 changes `port.py`, `layer.py`, and `AgenticForwarder`.

#### (c) Correlation plumbing

- **Requester side:** `send_request` already registers `self._outstanding[correlation] = name` **before** any `await` (`port.py:187`). Unchanged.
- **Producer side (no token, Option 3):** since the wire carries no token, the producer's demux must use the **request name**. On the real path the producer's `InboundRequest.correlation` is derived from the Interest's name (e.g. `sha256(name bytes)`), and `send_response` builds a `Content` named by the **same Interest name**. The requester's `_match_outstanding` then matches by **directional LPM** (PR-2) — correct for concrete names. **Under aggregation/wildcards there is no name-independent key; this is exactly the wire-format limitation (§7, A-012), and PR-1 must not pretend otherwise.**
- Correlation-carrying Nack: see (d).

#### (d) Correlation-carrying Nack OR documented limitation

- Today the Nack demux picks the oldest outstanding by dict order (`port.py:296-311`), which is arbitrary under concurrency. PR-1 chooses one:
  - **Preferred:** the Nack path records the **Interest name** it carried (`Nack` carries `name`; `decode_nack` returns `(name, reason)`, `NdnTlvEncoder.py:294-313`) and `_match_outstanding` is used **on the Nack name** to find the correlation (directional LPM). Fall back to dropping the Nack when no match, rather than mis-attributing. This is a real improvement and stays within the no-token design.
  - **Minimum acceptable:** keep oldest-outstanding but **document and test** the limitation (`test_nack_demux_ambiguous_is_documented`). ARCO requires one or the other; the preferred path is cheap and should be taken.

#### (e) Idempotent late/duplicate response

**Location:** `AgenticLayer._on_response` (`layer.py:213-220`) and `_on_failed` (`layer.py:230-237`).

- Before `record_response`, check the entry: if `entry is None` or `entry.terminated`, **drop** (return) without raising. This mirrors `maybe_complete`'s idempotency (`aggregation.py:146-147`).
- Optionally clear `self._leaf_index[correlation]` (or leave it; a dropped duplicate is fine). A test injects a second `ResponseArrived` for the same correlation after completion and asserts no exception and unchanged trace root.
- `ContextPIT.record_response` keeps raising on terminated entries (`context_pit.py:202-204`) — the **guard moves to the layer**, which is the correct owner (the PIT is a state machine and should stay strict). Document this split in the ADR-style rule.

#### (f) Deterministic TaskGroup failure semantics

**Location:** `submit_intent` concurrent branch (PR-3), plus a helper contract.

- Wrap each leaf send so that **any** of `{exception, `CancelledError` from a sibling, port-emitted failure}` maps deterministically:
  - Normal substrate failure already arrives as `RequestFailed` → `_on_failed` → `record_response(NULL)` (`layer.py:230-237`). Good.
  - A **cancelled/errored leaf task** must still emit a NULL. In the concurrent branch, `except BaseException` around each leaf task records `record_failed(parent, leaf_index)` and re-raises to the TaskGroup only after recording; or (cleaner) use `asyncio.gather(..., return_exceptions=True)` **inside** a TaskGroup-like shield so each leaf's exception is captured and mapped. Given AGENTS.md's caution about `ExceptionGroup` (`AsyncLayerProcess.py:98-107` mention in v1), **PR-1/D adopt**: each leaf coroutine catches its own exceptions, records a NULL via a new `AgenticLayer._record_leaf_failure(parent, leaf_index)`, and returns; the TaskGroup then sees no child exception in normal operation. A residual `ExceptionGroup` is unwrapped (ADR-007 discipline) and re-raised after NULL recording.
  - **Invariant:** `done` always resolves. Test: kill one leaf mid-flight (mock port raising/cancelling) → `Submit intent` returns a trace root with that leaf = `NULL_RESPONSE`, and no hang.

### 3.3 Verification (PR-1)

New tests under `agentic/tests/`:

| Test | Asserts |
|---|---|
| `test_producer_path_live_roundtrip.py` | SimulationBus: ambulance submit_intent → edge registered producer → `hub.invoke` **called** (spy) → Content returns → trace root verified; `submit_intent` returns within timeout |
| `test_inbound_request_translation.py` | `AgenticLayer.data_from_lower` receives a `[face_id, Interest]`, emits `InboundRequest` with preserved reply ref |
| `test_send_response_content.py` | `send_response` yields a `Content` on the wire; requester's port demuxes to the right correlation |
| `test_late_response_idempotent.py` | duplicate/late `ResponseArrived` after termination is dropped, no `ValueError`, root unchanged |
| `test_nack_correlation.py` | Nack with a name correlates via LPM; no match → drop, not mis-attribute (or documented oldest-outstanding test if minimum path chosen) |
| `test_taskgroup_failure_semantics.py` | one leaf fails/cancels → NULL at that leaf, `done` resolves, root deterministic |
| `test_submit_intent_serial_golden.py` | Existing serial path unchanged (root + dispatch count identical to pre-PR-1) |

Run: `python -m pytest agentic/tests/test_producer_path_live_roundtrip.py agentic/tests/test_taskgroup_failure_semantics.py -v`

**AC-PR1 required live round-trip specifically proves the *ambulance path → edge producer → Content back → Context PIT trace root* chain named in the task.**

---

## 4. Experiment D v3 — Concurrent fan-out + T_intent decomposition (real producer path)

### 4.0 Scope (unchanged intent, corrected dependency)

Two fan-out paths exist and are different code:
1. `AgenticLayer.submit_intent` (`layer.py:295-313`) — the real forwarding path over a `SubstratePort`. **D's primary target.**
2. `CardiacScenario._run_exchange` — structural in-process path bypassing the port. **Not modified for concurrency.**

**v3 change:** D's edge is no longer `_configure_edge_cs` (CS preload). D's edge is an `AgenticForwarder` with **registered producers** (PR-1), so the leaf actually reaches `hub.invoke` and `LatencyBackend` runs. This is what makes `T_service` real and `concurrent_publishable` reachable.

### 4.1 Concurrency mechanism + prepass (re-confirmed valid)

- **Mechanism: `asyncio.TaskGroup`** with the PR-1 failure semantics (§3.2f): any failed/cancelled leaf emits NULL, `done` always resolves. No hang.
- **Prepass (confirmed still valid and still required):** before dispatch, for every leaf precompute `correlation`, register `self._leaf_index[correlation] = (parent, leaf_index)`, call `self._pit.mark_forwarded(...)`, build the wire name. A fast response must not race an unpopulated mapping (`_on_response` drops on `mapping is None`). The prepass makes this impossible by construction.
- **I2 preserved:** commit at `layer.py:284-290` is synchronous and precedes the dispatch block. No `await` between commit and first dispatch. D adds a test asserting commit precedes all `RequestSent` events.
- **Index-keyed Merkle:** completion order never enters the digest (`context_pit.py:207`; `layer.py:213-237`). Out-of-order test retained (`MockSubstratePort(hold_terminals=True)` + reversed `release_terminals`, `mock/port.py:161-174`).
- Insertion point code shape unchanged from v2 §2.1; default `dispatch="serial"` preserves existing behaviour bit-for-bit.

### 4.2 `LatencyObserver` — updated for the now-real producer path

Placement unchanged: `agentic/agentic_layer/observer.py` (new; imports only stdlib + `agentic.port.events`; no `PiCN.*`, no LLM). Injected as `observer: LatencyObserver | None = None`; default `NullLatencyObserver`. `layer.py` calls hooks only.

Interface unchanged from v2 §2.2. **What changes in v3 is the sourcing of `t_service`:**

| Symbol | Source (v3, corrected) | Clock domain |
|---|---|---|
| `t0` (commit) | `time.perf_counter()` at `on_commit`, called immediately after `self._pit.commit(...)` returns (`layer.py:290`) | intake monotonic |
| `t_send` | `RequestSent.at` (`port.py:189`), captured in `_dispatch_event` (`layer.py:176-177`) | port clock |
| `t_response` | `ResponseArrived.at` (`port.py:287-294`), captured in `_on_response` (`layer.py:213`) | port clock |
| `t_service` | **Producer side, around `self._hub.invoke(...)` in `_on_inbound_request` (`layer.py:198-200`).** Because PR-1 makes the producer path reachable and D registers a `LatencyBackend`, this is now a **real, non-zero measurement** for D. | producer clock |
| `t1` (dispatch end) | `time.perf_counter()` at `on_dispatch_end` after the dispatch block | intake monotonic |
| `t_agg` | `time.perf_counter()` at `on_aggregate`, called from `_maybe_finish` after `maybe_complete` returns (`layer.py:239-245`) | intake monotonic |
| `t_intent` | `time.perf_counter()` at `on_complete` when `done` resolves (`layer.py:313`) | intake monotonic |

**t_emission — Finding 10 fix (T_decompose source).** `t_emission` (application calls `submit_intent`) is **not observable inside `submit_intent`**. v3 defines it as a **caller-threaded argument**: add an optional `emission_ts: float | None = None` parameter to `submit_intent`. The D runner captures `t_emission = time.perf_counter()` immediately before the call and passes it. If absent, `T_decompose` is **not reported** (`t_decompose_ms` omitted), rather than mis-sourced. Document that `T_decompose` includes the caller→layer call overhead definitionally.

**T_dispatch differs by mode definitionally.** Serial `T_dispatch ≈ k·L`; concurrent `T_dispatch ≈ L + overhead`. This is expected and is the measurement, not an artifact. Documented in the report.

**T_intent decomposition (single clock per tier, anti-double-count):**
```
T_decompose = t_commit - t_emission            # only when emission_ts supplied
T_dispatch  = t1 - t0                           # dispatch block wall-time
T_network   = mean_leaf(t_response - t_send)    # intake clock both ends
T_service   = mean_leaf(t_service)              # producer clock, offset-corrected in E
T_aggregate = t_intent - t1
```
- **View A (wall, exact):** `T_intent = T_decompose + T_dispatch + T_aggregate`.
- **View B (attribution):** `T_network`/`T_service` are decompositions **inside** `T_dispatch`; `T_dispatch_residual = max(0, T_dispatch − T_network − T_service)`. Never summed into View A.
- **D v3 honesty:** `T_network` remains **near-zero by construction** (SimulationBus); but `T_service` is **real and non-zero** because the producer backend is invoked via PR-1. **Therefore View B is no longer vacuous for D:** `T_service` carries the per-leaf service latency; the residual carries scheduling/fan-out. If, after PR-1, D's `T_network` is still unmeasurable, D **does not publish a full five-way decomposition** — it publishes a **four-way** decomposition with `T_network` explicitly marked `not_measurable` (see gate §4.5, Finding 7). No component is published as if measured when it is not.

### 4.3 Metric schema deltas v3

Backward-compatibility rule unchanged: `MetricEvent(kind, transport, seed, value, labels)` frozen. All new dimensions in `labels`.

**New `EventKind` values** (append to `events.py:14-26`):
```
"t_intent_ms", "t_decompose_ms", "t_dispatch_ms", "t_network_ms",
"t_service_ms", "t_aggregate_ms", "t_dispatch_residual_ms",
"fanout_serial_ms", "fanout_concurrent_ms", "fanout_speedup",
"leaf_inflight_peak", "leaf_overlap_fraction", "leaf_start_ms", "leaf_end_ms",
"observer_overhead_ms",
```
**Finding 11 fix:** `config_snapshot` is **NOT** an `EventKind`. It moves to **`RunMetadata`** (a `parameters["config_snapshot"]` mapping written once per run/campaign). `EventKind` stays a transport-scoped *observation* union; config is metadata, not an observation. `RunMetadata` already carries `parameters: dict` via the harness (`harness.py:94`, `metadata.py`). No schema abuse.

**`MetricsSnapshot` additive fields** (`metrics.py:31-60`, `None` defaults): `concurrent_publishable`, `fanout_serial_ms`, `fanout_concurrent_ms`, `fanout_speedup`, `leaf_inflight_peak`, `leaf_overlap_fraction`, `t_intent_ms`, `t_decompose_ms`, `t_dispatch_ms`, `t_network_ms`, `t_service_ms`, `t_aggregate_ms`, `t_dispatch_residual_ms`, `observer_overhead_ms`, `physical_publishable`.

Paper aliases: `concurrent_dispatch_publishable` → `concurrent_publishable`; `physical_deployment_publishable` → `physical_publishable`; `t_intent_total_ms` → `t_intent_ms`.

`compute_metrics` gains only an additive tail block; existing event sets produce byte-identical outputs. D aggregation lives in new `agentic/benchmark/concurrency.py`.

### 4.4 Sweep specification

- `--concurrency {serial,concurrent}` — default both (paired).
- `--leaf-latency-s {0,0.01,0.05,0.1,0.5}`.
- `--k 2,3,4,5,8`.
- `--seeds 1-5`.
- `--path happy`; `--transport bus`.
- **Control cell:** `leaf_latency_s = 0` in every campaign; expected ~1.0×. Bound to `control=true` (gate §4.5).
- **Zero-latency control cell is NOT run in LLM cells** (Finding 9) — it is redundant there (inference dominates; there is no transport-independent delay to isolate). This applies to E; D has no LLM.

**Paired-run semantics:** one record per `(seed, k, leaf_latency_s)` with both modes' times, decompositions, speedup. Valid iff both modes completed with identical `k` and byte-identical trace roots.

**CLI — `demo/run_concurrency.py`:**
```bash
python -m demo.run_concurrency \
  --seeds 1-5 --k 2,3,4,5,8 \
  --concurrency serial,concurrent \
  --leaf-latency-s 0,0.01,0.05,0.1,0.5 \
  --observer {on,off} \
  --transport bus \
  --out demo/results/concurrency.jsonl \
  --summary-json demo/results/concurrency_summary.json \
  --allow-dirty
```

### 4.5 Publishability gate `concurrent_publishable` (FIXED per Finding 8)

> `concurrent_publishable` is `True` **iff all** hold for a cell:
> 1. Both `fanout_serial_ms` and `fanout_concurrent_ms` exist for the **same** `(transport="bus", seed, k, leaf_latency_s)`.
> 2. **`control` is structurally bound to `leaf_latency_s` (Finding 8a):** the record's `labels["control"]` must equal `(leaf_latency_s == 0.0)`. The gate **recomputes** `control` from `leaf_latency_s` and refuses any record where the stored flag disagrees. A `leaf_latency_s=0` record can never satisfy conditions 3–7 (it is a control only). A `leaf_latency_s>0` record with `control=true` is rejected as malformed.
> 3. `leaf_latency_s > 0` **and** `control is False`.
> 4. Same leaf set: identical `k` and identical recorded `expected_subintent_set` digests.
> 5. `trace_root_equal is True`.
> 6. `fanout_speedup` finite and `> 0`.
> 7. **`T_service` is present and non-zero** (`t_service_ms` exists, `labels["measured"] == true`, value `> 0`), OR the record explicitly marks `t_service_ms` as `not_measurable` **and** publishes only the four-way decomposition (no View-B attribution claim). A record that presents a five-way decomposition with a zero/unmeasured `T_service` fails the gate. This directly closes Finding 7.
> 8. `labels["T_intent_view"] ∈ {"A"}` or explicit `T_dispatch_residual_ms` present.
>
> Failing any → `concurrent_publishable=False`; the figure layer refuses to render the speedup curve.

**Finding 8d — namespace test mechanism (specified precisely).** The D/E namespace isolation is enforced not by a vague "test" but by an **AST/import check**: a test parses each report generator with `ast`, collects the string literals used in `MetricEvent.kind == …` / record-`kind` comparisons, and asserts:
- `demo/generate_concurrency_report.py` consumes **exactly** `{"concurrency_run"}` (D),
- `demo/generate_physical_report.py` consumes **exactly** `{"physical_run"}` (E).
This is a static check on the generator's own source, so a careless edit fails CI. (Implementable with `ast` + a small visitor; no `PiCN.*` import.)

### 4.6 File plan (Experiment D v3)

| File | Action | Purpose |
|---|---|---|
| `agentic/binding/latency.py` | new | `LatencyBackend` wrapper (seeded jitter optional) |
| `agentic/binding/__init__.py` | modify | export `LatencyBackend` |
| `agentic/agentic_layer/observer.py` | new | `LatencyObserver` + `Null` + `Recording` |
| `agentic/agentic_layer/dispatch.py` | new | `DispatchMode` enum |
| `agentic/agentic_layer/layer.py` | modify | `dispatch` param; prepass; TaskGroup; observer hooks; **`emission_ts` param**; `data_from_lower` producer translation (PR-1); idempotent `_on_response`/`_on_failed` (PR-1) |
| `agentic/agentic_layer/events-adjacent` | modify | `InboundRequest.reply_ref` optional (PR-1) |
| `agentic/benchmark/events.py` | modify | append kinds (no `config_snapshot`) |
| `agentic/benchmark/concurrency.py` | new | D snapshot, speedup, gate |
| `agentic/benchmark/metrics.py` | modify | optional fields + aliases |
| `agentic/benchmark/__init__.py` | modify | exports |
| `agentic/adapters/picn/port.py` | modify | **PR-2** reverse clause deletion; **PR-1** response path |
| `demo/run_concurrency.py` | new | D runner |
| `demo/concurrency_topology.py` | new | D wiring: ambulance + **edge with registered producers** (PR-1) |
| `demo/generate_concurrency_report.py` | new | speedup-vs-k + stacked bars + honest captions |
| `agentic/tests/test_concurrency_paired.py` | new | paired same-leaf-set root equality, speedup, peak, overlap |
| `agentic/tests/test_latency_backend.py` | new | determinism, jitter seeding, conformance |
| `agentic/tests/test_latency_observer.py` | new | component definitions, single-clock, Null no-op |
| `agentic/tests/test_concurrent_publishable.py` | new | gate incl. control↔leaf_latency binding, T_service presence |
| `agentic/tests/test_submit_intent_i2_concurrent.py` | new | out-of-order via `hold_terminals`+reversed release |
| `agentic/tests/test_name_correlation.py` | new | **PR-2** prefix trap |
| `agentic/tests/test_report_namespace_isolation.py` | new | **AST** per-generator kind check |
| `demo/README.md`, `demo/EXPERIMENT_PLAN.md` | modify | document D, gate, control |

**Import discipline:** `layer.py` imports no `PiCN.*` beyond `PiCN.Processes`/`PiCN.Packets` (AC2); `observer.py`/`dispatch.py` import no PiCN.

### 4.7 Acceptance criteria (AC-PR3)
- `dispatch="serial"` default; golden test unchanged.
- Paired serial/concurrent same-leaf-set → byte-identical trace root.
- Speedup > 1 with `leaf_latency_s > 0`; ~1.0× at `leaf_latency_s = 0` (control).
- Out-of-order completion → correct root.
- Gate §4.5 green on a real paired cell, red on: unpaired, zero-latency-as-speedup, differing root, `control`/`leaf_latency_s` mismatch, zero `T_service` presented as five-way.
- **Live producer path:** `hub.invoke` invoked at the edge; `T_service > 0`.

---

## 5. Experiment E v3 — Physical testbed (≥2 edges, UDP, staged LLM)

### 5.1 Topology — `--edges N` (first campaign N=2)

Roles unchanged in kind; E depends on PR-1 so the producers are **registered capabilities**, not CS preloads.

- **Intake:** `AgenticLayer` + **one `PicnSubstratePort` per edge** + `RecordingLatencyObserver`.
- **Edges:** `AgenticForwarder` with `register_capability` for the capability prefixes it owns.
- **Producers:** registered backends (`DeterministicBackend`, `LatencyBackend`, `PydanticAIBackend`).
- **Capability-prefix ownership:** edge-1 owns `/cap/fwd/hospital/beds/h1..h4`, edge-2 owns `h5..h8`. The intake fans out over one port per edge; each port uses `peer_host=<edge IP>, peer_port=<edge UDP port>` (`port.py:99-107`).
- **Routing decision** at the intake is a true **directional LPM** over the registered prefix table (`longest_prefix_match`, `port.py:167-175`), which is the paper's demonstrated mechanism and matches PR-2.
- **N=1 single-edge** remains a supported control cell.
- **Distributed Context PIT stated honestly:** in-memory, node-local; the intake holds the authoritative Context PIT and aggregates; the Merkle root is computed at the intake. Not claimed: cross-node replication/consensus/distributed root.

### 5.2 Clock-offset correction
Unchanged from v2 §3.2: intake-side timestamps single-clock; producer-side `t_service` offset-corrected via NTP/chrony measurement before each campaign; residual skew recorded; runs above threshold (default 1 ms) excluded and counted. `T_network` needs no cross-clock (both ends intake events).

### 5.3 LLM staging + cost matrix (Finding 9)

**CLI — `demo/run_physical.py`:**
```bash
python -m demo.run_physical \
  --backend {deterministic,llm} \
  --llm-placement {on-pi,off-pi} \
  --edges 2 --seeds 1-5 --k 8 \
  --runs-per-cell 30 \
  --model-config deploy/pi/group_vars/llm/model.toml \
  --transport udp --run-id <id> \
  --out demo/results/physical/<run-id>.jsonl
```
- **Stage A:** `--backend llm --llm-placement on-pi` (Ollama per producer on the Pi).
- **Stage C:** `--backend llm --llm-placement off-pi` (off-Pi GPU host on LAN).
- **AVOID B** (dedicated on-LAN LLM *node* distinct from producer) — rejected; would conflate `T_network` with `T_service`.
- **`--leaf-latency-s` is dropped from LLM cells** (Finding 9): inference dominates; the artificial delay is redundant and would be double-counted.
- **TOML contract** (`pydantic_ai_backend.py:32-55`, local-first):
  ```toml
  [model]
  preference = ["ollama:llama3.2", "openai:gpt-4o-mini"]
  ```
- **Per-leaf split:** `inference_ms` (around `backend.invoke` at producer), `transport_ms` (intake-clock `t_response − t_send`), `total_ms`. Inference is never credited to the overlay.

**Cost matrix (Finding 9) — explicit, per campaign:**

Let `D_cell` = per-run wall-time dominated by LLM inference (measured in Stage A as `T_intent`), `n` = runs/cell, `C` = cells.

| Campaign | cells `C` | runs/cell `n` | est. per-run | est. total (serial) |
|---|---|---|---|---|
| E-DET (`--backend deterministic`) | `seeds 5 × k 8 × edges 1 = 5` | 30 | < 1 s | ~2.5 min |
| E-LLM-A (on-Pi) | `5 × 1 × 2 placements = 10` | **30** | 3–30 s (model-dependent) | **25 min – 2.5 h** |
| E-LLM-C (off-Pi) | 10 | **30** | 1–10 s | 8 min – 50 min |

**Decision and justification.** `n=30` is retained for **deterministic** cells (cheap). For **LLM** cells the estimate above is **documented and bounded**, and `n` is **reduced to the minimum defensible** with disclosure:
- Default LLM `n = 15` (not 30 coverage: report `n`, median, mean, stdev, and a bootstrap CI; state in the caption that LLM cells use `n=15` because per-run cost is inference-dominated and the substrate share is the quantity of interest, not a tight distribution estimate).
- The gate requires `n ≥ MIN_LLM_RUNS (=15)` for LLM cells and `n ≥ MIN_PHYSICAL_RUNS (=30)` for deterministic cells, each disclosed in the record.
- A `--runs-per-cell` override is allowed upward; a **cost preflight** (dry-run: one warm run per cell) prints the projected wall-clock and **refuses to start** a campaign whose projection exceeds `--max-hours` (default 6 h) unless `--ack-cost` is passed. This is the A-011 style (refuse loudly, do not silently run for days).

### 5.4 `physical_publishable` gate (FIXED per Finding 8)

> `physical_publishable` is `True` **iff**:
> 1. `metadata.transport == "udp"`; `--edges >= 1` recorded; **per-node run markers** recorded for every node.
> 2. **Deployed-commit attestation (Finding 8c):** every node's run marker records the **`PICN_COMMIT` actually deployed and the commit on disk on that node** at run time, verified equal. A clean local tree is **not** sufficient. Any node whose marker is missing or whose disk commit ≠ `PICN_COMMIT` fails the gate.
> 3. Intake `submit_intent` completed with `trace_root_verified is True`.
> 4. Per-cell run count meets the type-specific threshold: deterministic cells `n ≥ 30`, LLM cells `n ≥ 15`, same `(seed, k, backend, llm_placement, edges)`; report `n / median / mean / stdev`.
> 5. All reported `T_intent` components present per run (see condition 7 for the unmeasurable-component rule); for `backend="llm"`, `inference_ms`/`transport_ms` split present per leaf.
> 6. Host clock skew recorded and below threshold; runs above threshold excluded and counted.
> 7. **Name-based routing proof (Finding 8b — replaces the gameable "one non-intake leaf"):** at least one leaf is answered by a **producer whose declared capability prefix is owned by a non-intake node, and that ownership is recorded in that node's per-node run marker**. The gate cross-checks the answering producer's node marker's `owned_prefixes` and `deployed_commit` — so a leaf answered locally, or by a producer not attested as owning that prefix, does not satisfy it.
> 8. For deterministic multi-edge cells: ≥ 2 edges actually served at least one leaf each (recorded per-edge leaf counts).
>
> Failing any → gate `False`; figures refuse to render; record retained for audit.

### 5.5 Honest framing discipline (tiers)
Unchanged table from v2 §3.4 (D=bus/illustration, E=udp/deployment; separate JSONL namespaces; never share a number). Namespace isolation enforced by the §4.5 **AST** per-generator test. Caption rules verbatim.

### 5.6 Provisioning structure, env contract, bring-up
As v2 §3.6 (inventory `[intake][edges][producers][llm][observer]`, roles `picn_base/picn_node/picn_measure`, runbooks `bringup/teardown/clock_sync`, env contract `PICN_*`), **plus** per-node run markers written to `PICN_RESULTS_DIR/markers/<run_id>-<host>.json` containing `{host, role, deployed_commit, disk_commit, owned_prefixes, started_at, ended_at}`. These markers are what conditions 1/2/7 consume.

### 5.7 Acceptance criteria (AC-PR4)
- E-DET campaign completes within the cost estimate; `physical_publishable=True` on a valid multi-edge cell.
- Gate red on: `< 2` eligible edges for a multi-edge cell; missing/mismatched deployed-commit marker; leaf answered by a node not owning the prefix; LLM cell `n < 15`; skew above threshold.
- AST namespace test green.
- LLM cost preflight prints and enforces `--max-hours`.

### 5.8 File plan (Experiment E v3)
As v2 §3.7, plus: `demo/run_physical.py` (cost preflight, `--max-hours`, `--ack-cost`), `demo/physical_node.py` (writes per-node run markers), `agentic/benchmark/physical.py` (gate conditions per §5.4), `agentic/tests/test_physical_gate.py` (all conditions incl. deployed-commit + prefix-ownership), `agentic/tests/test_run_physical_cli.py` (namespace + preflight).

---

## 6. Metric schema evolution v3 (backward-compatible deltas)

### 6.1 `MetricEvent` — frozen
Unchanged `(kind, transport, seed, value, labels)`.

### 6.2 `EventKind` — additive, **no `config_snapshot`**
Append the §4.3 kinds. `config_snapshot` is **not** here (Finding 11). It lives in `RunMetadata.parameters["config_snapshot"]`.

### 6.3 `MetricsSnapshot` — additive optional fields
Per §4.3. `compute_metrics` additive tail only; byte-identical on old event sets.

### 6.4 Coexistence table

| Metric family | Gate | Transport | Populated by | Status |
|---|---|---|---|---|
| `m1_latency_ms` | none (UDP absolute) | bus/udp | harness | unchanged |
| `m3_overhead_ratio` | `m3_publishable` | bus | `run_paired_bus` | untouched |
| `t_intent_ms` + decomposition | `concurrent_publishable` (D) / `physical_publishable` (E) | bus/udp | D/E | new, tier-scoped |
| `concurrent_publishable` | new | bus | D | new, independent |
| `physical_publishable` | new | udp | E | new, independent |

### 6.5 RunMetadata delta
`parameters["config_snapshot"]` (one per campaign) and `parameters["deployment"]` (E: `{picn_commit, run_id, edges, per_node_markers}`). No EventKind change.

---

## 7. Honesty & anti-strawman checklist v3

**WIRE-FORMAT LIMITATION (mandatory — Dennis; must appear here, in A-012, and in the paper):**

> In-band correlation tokens require extending the ICN wire format (an Interest/Content parameter that is echoed by producers). The ICN community treats wire-format changes with strong caution, because even additive TLVs raise interoperability, cache-consistency, and deployment concerns across a global forwarding fabric. This work therefore deliberately ships **directional longest-prefix matching** — which is correct for concrete capability names — and presents the token as a **forward-looking design** (ADR A-012), not a deployed mechanism. Resolving wildcard and aggregation demux without widening the wire format remains an open problem and is called out as future work.

**Framing rule:** state it plainly and early. Do **not** claim present-tense wildcard-readiness. The current mechanism is correct and wire-compatible; the token is the proposed direction with the tension acknowledged.

**Experiment D:**
- [ ] Serial baseline is the actual `submit_intent` serial path.
- [ ] Same leaf set/k/`leaf_latency_s`/backend/seed across modes.
- [ ] `leaf_latency_s > 0` for any published speedup; zero-latency cells are controls, bound by the gate.
- [ ] Trace roots byte-identical.
- [ ] Speedup never across different sweeps.
- [ ] **Real producer backend invoked; `T_service` non-zero and measured** (otherwise no five-way decomposition is claimed).
- [ ] `T_network` stated near-zero by construction; if unmeasurable, marked `not_measurable` and excluded from published decomposition.
- [ ] `T_decompose` only when `emission_ts` supplied; otherwise omitted.
- [ ] Observer overhead bounded by calibration; reported.
- [ ] Out-of-order completion exercised; root verified.
- [ ] Peak in-flight/overlap measured.
- [ ] Flattening at large k reported.
- [ ] Gate cannot be satisfied by zero-latency or unmeasured-service cells.

**Experiment E:**
- [ ] ≥ 2 edges served (multi-edge cell); per-edge leaf counts recorded.
- [ ] `transport=udp`; no bus in E.
- [ ] `T_network` real, share reported.
- [ ] LLM split present; overlay not credited with inference.
- [ ] Run counts: det ≥ 30, LLM ≥ 15; n/median/mean/stdev reported.
- [ ] Clock skew thresholded; excluded counted; `T_service` offset-corrected.
- [ ] Deployed-commit attestation per node (not just clean tree).
- [ ] Name-based routing proven by prefix-ownership in the answering node's marker.
- [ ] Context PIT intake-local; cold-start exclusion disclosed.
- [ ] Gate enforced before figures.
- [ ] D and E never share a table/figure/ratio; AST namespace test.
- [ ] **Wire-format limitation stated prominently.**

**Cross-cutting:**
- [ ] D does not claim to beat Temporal/Step Functions.
- [ ] E does not claim superiority over D.
- [ ] No "we beat X" language.
- [ ] All gates documented in `EXPERIMENT_PLAN.md` and `demo/README.md`.
- [ ] A-012 reverse clause absent; token presented as future work.

---

## 8. Risks & open questions v3

**Closed by v3 / PR-1:**
- ARCO BLOCKER 1 (dead producer path) — closed by PR-1 §3.2a (corrected location).
- ARCO BLOCKER 2 (`send_response` no transmit) — closed by PR-1 §3.2b.
- ARCO MAJOR 5 (late/duplicate crash) — closed by PR-1 §3.2e (+ Nack policy §3.2d).
- ARCO MAJOR 6 (TaskGroup hang) — closed by PR-1 §3.2f.
- ARCO MAJOR 7 (vacuous View B) — closed by real `T_service` + gate condition 7.
- ARCO MAJOR 8 (gameable gates) — closed by §4.5/§5.4 (control binding, prefix-ownership proof, deployed-commit attestation, AST namespace test).
- ARCO MAJOR 9 (cost) — closed by §5.3 cost matrix + preflight.
- ARCO MINOR 10 (`t_emission`) — closed by `emission_ts` threading.
- ARCO MINOR 11 (`config_snapshot`) — closed by moving to RunMetadata.
- ARCO MINOR 12 (bundle) — closed by the four-PR spine.
- ARCO MAJOR 3 / A-012 token — addressed by Option 3 (future work; §7 + A-012).

**Retained risks (live):**
1. **PR-1 unknown: layer-stack delivery of `queue_higher` to `AgenticLayer.data_from_lower` with face id preserved.** First PR-1 spike; if the stack does not preserve the reply face, PR-1 must add a small plumbing path. HIGH until proven by the live round-trip test.
2. **Producer Content injection through the forwarder ICN layer.** `handle_content` requires a PIT entry (`ICNLayerCore.py:146`); the `_interest_to_app` path records one (`ICNLayerCore.py:120-124`), read but unproven. PR-1 test.
3. **Bounded queues** `DEFAULT_INBOUND_SIZE=64` (`layer.py:47`), `MAX_CONTEXT_PIT_ENTRIES=256` (`context_pit.py:28`) PROVISIONAL; `port.py:319` awaits a full queue → possible serialization. D reports queue-bound effects; E confirms on hardware.
4. **`LatencyBackend` placement** pinned to the producer backend; test asserts invocation inside `hub.invoke`, not `send_request`.
5. **Clock domain mismatch for E** (`port.py:75` monotonic); offset correction + error bars; threshold open.
6. **Goldens:** `dispatch="serial"` default; golden test re-run of the cardiac bus (root + dispatch count). Note: the cardiac bus itself still uses CS preload and is **not** migrated to registered producers in D/E — it stays the A/B/C structural runner. D/E use a new topology.
7. **LLM `n=15`** is a judgment call; the gate discloses it. If a venue demands 30, the preflight will surface the cost and Dennis decides.
8. **Encoder additivity** is moot for the shipped work (no wire change), but remains the largest unknown for the future token milestone.

**Open questions for Dennis:**
1. `InboundRequest.reply_ref` as a new optional event field vs a side table (PR-1 §3.2a). Recommendation: optional field (internal event, additive).
2. Nack correlation: preferred name-based LPM demux vs documented oldest-outstanding limitation (PR-1 §3.2d). Recommendation: name-based.
3. LLM `n=15` vs `30` (PR-4). Recommendation: 15 with bootstrap CI and disclosure.
4. Clock-skew threshold for E (default 1 ms).
5. `--max-hours` preflight default (6 h).

---

*End of DESIGN v3. No implementation code written — every Go/Python implementation item is CRAFT's. Every claim about existing code cites a verified file:line or is flagged in §0/§8. The PR-1 correction (producer translation lives in `AgenticLayer.data_from_lower`, not `PicnSubstratePort._deliver_uplink_packet`) is the single most important architectural change from v2 and is grounded in `AgenticForwarder.py:120-152`, `ICNLayerCore.py:120-124`, and `layer.py:315-328`.*