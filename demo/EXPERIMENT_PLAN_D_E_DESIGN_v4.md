# DESIGN v4 — Experiments D & E: Concurrent Fan-Out, T_intent Decomposition, and the Physical Testbed for Agentic Routing

**Author:** DESIREE 🏗️ (architecture & design)
**Status:** DESIGN v4 — **FINAL, build-ready** (supersedes v3; not implemented)
**Repo:** `gh-repositories/PiCN` (fork of Basel PiCN)
**Target:** revise COMMAG-26-00578 (rejected) + TNSM/Computer Networks reframing
**Date:** 2026-09-12
**Incorporates:** Decisions 1–8 (v1/v2), Dennis' Option C producer-path decision, Dennis' Option 3 (staged) A-012 decision, the ARCO v1 BLOCK (12 findings), the **ARCO v3 CONCERN re-review (NF-1..NF-5)**, and the mandatory wire-format transparency note.
**Supersedes:** `EXPERIMENT_PLAN_D_E_DESIGN.md` (v1), `_v2.md` (v2), `_v3.md` (v3).
**Self-contained:** §9 carries the full v1 BLOCK outcome and the v3 CONCERN outcome + NF-1..NF-5 dispositions, so this file needs no other design doc to be understood.

**v4 headline:** v3 resolved the producer path *on paper* but left ARCO five residual ambiguities, two of them fatal to any implementation that reads the doc literally: (NF-1) a `send_response` "Case B" that pushes `Content` onto a stack with no ICN layer, and (NF-2) an under-specified `data_from_lower` input contract. v4 **deletes Case B**, **pins the exact `[face_id, Interest]` input shape with a verified producer-path trace**, and **makes PR-1's first deliverable a mandatory live spike** that must pass before any D/E file is touched. It also resolves the `reply_ref` union question (Option 2 chosen, justified), and scopes `physical_publishable` condition 7/8 correctly (NF-5).

---

## 0. Verification notes (what I read this revision, what I could NOT verify)

### 0.1 Re-verified against code for v4 (file:line)

Every claim below was re-read in this revision; the producer-path trace (§0.2) is new in v4 and is the basis of NF-2's fix.

**Requester (intake) side — send path:**
- `submit_intent` is serial: `layer.py:295-313` — a plain `for` loop with `await self._port.send_request(...)`. Commit (`layer.py:284-290`) is synchronous and precedes the loop (I2). `done` future created at `layer.py:291-293`, awaited at `layer.py:313`.
- `PicnSubstratePort.send_request` registers `self._outstanding[correlation] = name` **before** any `await` (`port.py:187`), emits `RequestSent` (`port.py:188-190`), pushes `Interest(to_picn_name(name))` onto `self._lstack.queue_from_higher` (`port.py:191-193`). `send_request` does `_ = payload` (`port.py:210`) — **no Interest parameter rides the wire today**.
- Client stack is `AsyncLayerStack([chunk, timeoutprevention, packetencoding, link])` (`port.py:91-98`) — **no ICN layer** (this fact is the whole of NF-1).

**Requester side — receive path:**
- `_deliver_uplink_packet` (`port.py:271-311`) is the **response demux**: it reads `self._lstack.queue_to_higher` in `_read_uplink` (`port.py:321-328`) and turns `Content` into `ResponseArrived` (`port.py:287-294`). It is **not** the producer-side Interest translation point. (v3 §0 Correction 1 stands.)
- `_match_outstanding` (`port.py:262-269`): forward clause `interest_name == content_name or interest_name.is_prefix_of(content_name)` at **`port.py:265`**; reverse defect `content_name.is_prefix_of(interest_name)` at **`port.py:267`**. A-012's forward-clause citation is correctly anchored at `port.py:265`. (`longest_prefix_match`'s legitimate `prefix.is_prefix_of(name)` is at `port.py:172`.)

**Producer (edge) side — the verified end-to-end trace (NEW in v4, closes NF-2's evidentiary gap):**
1. `AgenticForwarder.__init__` assembles the stack `[agentic, nfn, chunk, timeoutprevention, icn, packetencoding, link]` (`AgenticForwarder.py:142-152`) and sets `self.icnlayer._interest_to_app = True` (`AgenticForwarder.py:124`).
2. `AsyncLayerStack.__init__` wires `upper.queue_from_lower = q_to_upper` and `lower.queue_to_higher = q_to_upper` for each adjacent pair (`AsyncLayerStack.py:62-71`). Therefore ICN's `queue_to_higher` **is** NFN's `queue_from_lower`, and NFN's `queue_to_higher` **is** AgenticLayer's `queue_from_lower`.
3. An Interest arriving at the edge ICN from the link enters `handle_from_lower` (`ICNLayerCore.py:46-66`), which calls `handle_interest_from_lower(face_id, interest, from_local=False, has_to_higher=...)` (`ICNLayerCore.py:59-61`).
4. No CS hit (`ICNLayerCore.py:108-113`), no existing PIT (`:114-119`); with `_interest_to_app is True and has_to_higher` (`:120`): the ICN records a PIT entry `self.pit.add_pit_entry(interest.name, face_id, interest, local_app=from_local)` — **`local_app=False`** — and emits `Outbound("queue_higher", [face_id, interest])` (`ICNLayerCore.py:122-123`). **The face id is element `[0]`.**
5. `AsyncBasicICNLayer._apply_outbound` routes `"queue_higher"` to `self.queue_to_higher.put(out.item)` (`AsyncBasicICNLayer.py:77-79`); that queue is NFN's `queue_from_lower`.
6. NFN `handle_from_lower` (`NFNLayerCore.py:80-103`) → `handle_interest`; the name is not an R2C request and its last component is not `b"NFN"`, so NFN passes it upward **unmodified** with `direction = "queue_higher" if has_to_higher else "queue_lower"` and the **same `packet_id` (= face id)** (`NFNLayerCore.py:117-123`).
7. `AsyncLayerProcess.run`'s `_pump_lower` calls `await self.data_from_lower(self.queue_to_lower, self.queue_to_higher, data)` with `data == [face_id, Interest]` (`AsyncLayerProcess.py:108-111`). **`AgenticLayer.data_from_lower` (`layer.py:315-328`) is reached with `[face_id, Interest]` and the face id preserved.** This is the NF-2 contract and it is now read-verified.

**Producer side — the response return path (read, unproven; NF-3 spike target):**
- `_on_inbound_request` (`layer.py:179-211`) parses the capability name, resolves the registry entry, calls `self._hub.invoke(...)` (`layer.py:198-200`), builds a JSON body (`layer.py:201-207`), and calls `self._port.send_response(event.correlation, …)` (`layer.py:208-211`). It is the **only** caller of `hub.invoke` (`layer.py:198`).
- `CapabilityProducerHub.invoke` → `self.registry.invoke(...)` (`producer.py:82-93`).
- `handle_content` (`ICNLayerCore.py:142-157`): with a PIT entry present, for each PIT face `i`, if `has_to_higher and pit_entry.local_app[i]` it emits `Outbound("higher", [face_id, content])`, **else** `Outbound("lower", [pit_entry.faceids[i], content])` (`ICNLayerCore.py:150-154`). The producer's PIT entry for the *incoming* Interest has `local_app=False` (step 4). So a Content injected at the ICN **from above** (`handle_from_higher`, `ICNLayerCore.py:40-41`, which passes `from_local=True, has_to_higher=True`) resolves to the **`else` branch → `Outbound("lower", [requester_face, content])`**. The `has_to_higher=True` argument does **not** divert it, because the diverted branch is gated on `pit_entry.local_app[i]`, which is `False` for that face.
- **Conclusion (read-verified, still test-unproven):** the response path is `AgenticLayer → queue_to_lower → NFN handle_from_higher → chunk → timeoutprevention → ICN data_from_higher → handle_content → Outbound("lower", [requester_face, content]) → link → requester`. NF-3 requires this be proven live **before** any D/E file is touched.

**Other re-verified items carried from v3 (unchanged, still true):**
- `Interceptor`/producer-side backend invocation only at `layer.py:198-200`.
- Wire reality: `Interest(name, wire_format)` no parameter field (`Interest.py:10-16`); `Content(name, content, wire_format)` (`Content.py:10-18`); `NdnTlvEncoder.encode_interest` Nonce+Name only (`NdnTlvEncoder.py:149-166`); `decode_interest` Name only (`NdnTlvEncoder.py:271-279`).
- `SubstrateEvent` closed union (`events.py:99-102`); `EventKind` closed literal; `labels: dict[str, Any] | None` is the additive channel.
- AC1 forbids `agentic.benchmark` importing `PiCN.*`; AC2 constrains `layer.py` imports; AC3 forbids LLM clients outside `binding/pydantic_ai_backend.py`.
- `Interceptor` at `layer.py:198-200`; `ContextPIT.record_response` raises `ValueError("entry already terminated")` on a terminated entry (`context_pit.py:202-204`); `terminate` is idempotent (`context_pit.py:225-226`); `maybe_complete` idempotent (`aggregation.py:146-147`); `done` guarded by `if not fut.done()` (`layer.py:244`).
- The cardiac demo answers hospital leaves from the edge **CS** via `MgmtClient.add_new_content` (`cardiac_bus_topology.py:150-166`) and never calls `edge.register_capability` — hence `hub.invoke` is never reached in the existing demo. D/E must use **registered producers**.
- `AsyncLayerProcess.run` deliberately avoids `TaskGroup` to keep exceptions unwrapped (`AsyncLayerProcess.py:95-107`); D/E's `TaskGroup` use is inside `submit_intent`, not the layer run loop, so it does not conflict, but it inherits the `ExceptionGroup` unwrap discipline.

### 0.2 Could NOT verify (flagged, no guessing)

1. **That the §0.1 response path returns Content to the requester at runtime** (read says yes; NF-3's mandatory live spike is the proof). Until the spike passes, the producer path is *designed*, not *working*.
2. **That the requester's client stack (`chunk/timeoutprevention/packetencoding/link`, `port.py:91-98`) accepts the returned `Content` and surfaces it at `queue_to_higher`** for `_read_uplink` (`port.py:321-328`) to demux. The client stack is exercised today by the fetch path; the *producer-originated* return into this exact stack is part of the spike.
3. **That a forwarder-backed response seam can put onto `AgenticLayer.queue_to_lower` without confusing NFN's `queue_from_higher`** — the seam's mechanism is specified in §3.2b; its behaviour is NF-3 spike assertion (b).
4. **Bounded-queue interaction** at the producer: `AsyncLayerStack` bounds every queue at `DEFAULT_QUEUE_SIZE=128` (`AsyncLayerStack.py:23`); the agentic inbound queue is `DEFAULT_INBOUND_SIZE=64` (`layer.py:47`); `MAX_CONTEXT_PIT_ENTRIES=256` (`context_pit.py:28`). All PROVISIONAL.
5. **No `LatencyBackend`, no `LatencyObserver`, no `runs-per-cell` UDP policy, no `deploy/`, inventory, or Pi artifacts exist.** Greenfield.
6. **A-012's encoder additivity** remains the future token milestone's largest unknown (moot for the shipped work: no wire change).

---

## 1. Goal & reviewer mapping (intent unchanged from v3)

### 1.1 The two skeptical-committee objections this must kill

> **(a)** "The strongest ICN win is untested. Everything is a single in-flight intent on a shared bus. The actual benefit of name/fan-out in an ICN/agentic overlay is untested."
>
> **(b)** "What does your solution add? Temporal / Step Functions / SPIFFE / OTel / MCP already orchestrate fan-out and time it."

D and E jointly attack (a). The **T_intent decomposition** attacks (b) by reporting what share of an intent's end-to-end time each substrate stage contributes. **v4 keeps the decomposition non-vacuous** by making `T_service` genuinely measurable (real registered producer path, PR-1) and `T_network` real in E.

### 1.2 Reviewer complaint → experiment mapping

| Complaint | Source | Answered by | How |
|---|---|---|---|
| R1 #6 / #7 — weak comparison vs Temporal / Step Functions / SPIFFE / OTel / MCP; **"what does Agentic Routing add?"** | R1 | **D + E via T_intent decomposition** | `T_intent` decomposed into stages; the substrate's share is reported numerically. v4 guarantees `T_service` is non-zero and measured (real producer backend), and `T_network` is real in E. |
| Committee concurrency objection — single in-flight intent, fan-out benefit untested | committee | **D (primary), E (secondary)** | D produces a **speedup-vs-k curve** on the exact leaf set with a paired serial baseline, up to k=8, over a **real producer path** (PR-1). E repeats over real UDP. |
| R1 #3 / #4 — Context PIT omission-accountability / attestation limits underspecified | R1 | **D (partial), E (partial)** | D stress-tests omission accounting under out-of-order completion at k up to 8; E across real nodes. Deeper attestation remains C's remit. |
| R3 #3 / R4 — no implementation / simulation / PoC | R3, R4 | **D AND E** | Two PoCs at different fidelity tiers. |
| R4 — emergency scenario too high-level | R4 | **E** | Physical multi-node deployment with named roles; capability-name routing across ≥2 physical edges. |
| "Your speedup is a measurement artifact" | anticipated | **D zero-latency control cell (fixed gate)** | `leaf_latency_s=0` cells must yield ~1.0× and are **structurally bound** to `control=true`; they can never satisfy a speedup claim. |

### 1.3 Non-goals (explicit)

- D does **not** claim real network behaviour. Its `T_network` is near-zero by construction; D's honest claim is **dispatch/aggregation/decomposition isolation**. D's `T_service` **is** real once PR-1 lands.
- E does **not** claim a controlled comparison against D. It is a physical deployment measurement.
- Neither touches the NFN combine microbenchmark (`m3` / `nfn_stack_overhead`).
- **The in-band wildcard-correlation token is NOT delivered in this work** (Dennis Option 3). A-012 ships directional LPM now; the token is future work (§7, A-012).
- The producer Content injection is **not** an ICN wire-format change: it injects a standard `Content` into the forwarder's own stack so the forwarder's PIT resolves the return face. It changes no PiCN packet format, FIB/PIT/CS logic, or forwarding semantic.

---

## 2. Milestone / PR plan and dependency order

Work splits into four sequential PRs, each a separate concern (AGENTS.md "one concern per commit"). No PR depends on a later PR.

```
PR-1  G.3 producer wiring ──► PR-2  A-012 demux LPM ──► PR-3  Experiment D ──► PR-4  Experiment E
      (SPIKE FIRST, then         (port.py: delete reverse     (run_concurrency,   (run_physical,
       AgenticLayer/port/         clause → directional LPM;    concurrency.py,     physical.py,
       AgenticForwarder wiring;   no wire change)              observer,           deploy/pi/)
       hardening)                                            LatencyBackend)
```

### PR-1 — G.3 producer wiring (spike-gated)

**Scope.** (0) **Mandatory live spike** proving the producer path and response return path (§3.1). (1) Make a capability Interest forwarded to a node with a registered capability reach `AgenticLayer._on_inbound_request` → `hub.invoke`. (2) Transmit the response as a `Content` the requester's port demuxes to the originating correlation, via a **forwarder-backed response seam** on `AgenticLayer` (Case A only). (3) Hardening: idempotent late/duplicate response, Nack correlation policy, deterministic TaskGroup failure semantics. Contains **no wire-format change**.

**Files:** `PiCN/ProgramLibs/AgenticForwarder/AgenticForwarder.py`, `agentic/agentic_layer/layer.py`, `agentic/adapters/picn/port.py`, `agentic/port/events.py` (comment-only edit if any), tests under `agentic/tests/`.

**AC-PR1 (each is a runnable command or named test):**
1. **SPIKE GATE (mandatory, runs first):** `python -m pytest agentic/tests/test_producer_path_spike.py -v` — two assertions, both must pass:
   - **(a)** `queue_higher` reaches `AgenticLayer.data_from_lower` with `data == [face_id, Interest]` and `data[0]` is the requester's face id (§3.1 assertion (a)).
   - **(b)** an injected `Content` sent down the forwarder stack reaches `handle_content` and is emitted `Outbound("lower", [requester_face, content])` (§3.1 assertion (b)).
2. `python -m pytest agentic/tests/test_producer_path_live_roundtrip.py -v` — SimulationBus: ambulance `submit_intent` → edge `AgenticForwarder` with a **registered** `DeterministicBackend` → `hub.invoke` **called** (spy) → `Content` returns → ambulance Context PIT trace root verified; `submit_intent` returns within timeout.
3. `python -m pytest agentic/tests/test_inbound_request_translation.py -v` — `data_from_lower` receives `[face_id, Interest]`, emits `InboundRequest` with **reply ref preserved and equal to `data[0]`**.
4. `python -m pytest agentic/tests/test_send_response_content.py -v` — `send_response` yields a `Content` on the wire; requester port demuxes it to the correct correlation.
5. `python -m pytest agentic/tests/test_late_response_idempotent.py -v` — duplicate/late `ResponseArrived` after termination dropped, no `ValueError`, root unchanged.
6. `python -m pytest agentic/tests/test_nack_correlation.py -v` — Nack correlates by name-LPM; no match → drop, not mis-attribute.
7. `python -m pytest agentic/tests/test_taskgroup_failure_semantics.py -v` — one leaf fails/cancels → `NULL_RESPONSE` at that leaf, `done` resolves, root deterministic.
8. `python -m pytest agentic/tests/test_submit_intent_serial_golden.py -v` — serial path unchanged (root + dispatch count identical to pre-PR-1).
9. `python -m pytest agentic/tests/test_picn_adapter.py agentic/tests/test_port.py agentic/tests/test_mock_adapter.py agentic/tests/test_layer_plugin_mock.py agentic/tests/test_nfn_passthrough.py -v` — no regression.

**Does NOT absorb:** the A-012 reverse-clause deletion (PR-2), any `LatencyObserver`/`LatencyBackend` (PR-3), any experiment runner/topology (PR-3/PR-4), any wire-format change (explicitly future work).

**Unblocks:** PR-3 (`T_service` measurable; a registered producer is actually invoked), PR-4 (UDP return path exists).

### PR-2 — A-012 demux LPM (bug fix only)

**Scope.** Delete the reverse clause `content_name.is_prefix_of(interest_name)` at `port.py:267`. Keep the forward directional LPM (`port.py:265`). Document `MockSubstratePort`'s outstanding semantics as name-independent-by-correlation (it does not use `_match_outstanding`). **No wire change.** A-012 is already placed and revised; PR-2 implements it.

**Files:** `agentic/adapters/picn/port.py`, `agentic/tests/test_name_correlation.py` (new), A-012 (already placed at `Agentic-ICN-Network/agentic_PiCN/decisions/A-012-name-correlation-and-wildcards.md`).

**AC-PR2:**
1. `! grep -n "content_name.is_prefix_of" agentic/adapters/picn/port.py` (asserts **absence** of the defect; a bare `is_prefix_of` grep cannot distinguish it because of `port.py:172`).
2. `python -m pytest agentic/tests/test_name_correlation.py::test_prefix_trap -v` (h1 vs h10) passes.
3. `python -m pytest agentic/tests/test_name_correlation.py::test_directional_lpm -v` passes.
4. The two future-token tests exist and are **`skip`** (not `xfail`): `test_token_exact_match`, `test_lpm_fallback_no_token`.
5. `python -m pytest agentic/tests/test_port.py agentic/tests/test_mock_adapter.py -v` green.

**Does NOT absorb:** any producer wiring (PR-1), any token/wire carrier (future work), any experiment metric.

**Unblocks:** PR-3 and PR-4 (unambiguous demux). Note: PR-3 uses distinct `h*` names anyway, but the fix is required for E's ≥2-edge name routing and the paper's correctness claim.

### PR-3 — Experiment D

**Scope.** `LatencyBackend`, `LatencyObserver`, `submit_intent` concurrency (`TaskGroup`) + prepass, `concurrency.py` snapshot/gate, `demo/run_concurrency.py`, `demo/concurrency_topology.py`, report generator, tests. Detailed in §4.

**AC-PR3:** §4.7.

**Does NOT absorb:** producer wiring (PR-1), demux fix (PR-2), any physical/UDP concern (PR-4).

**Unblocks:** PR-4 (observer + `LatencyBackend` + metric schema reused).

### PR-4 — Experiment E

**Scope.** `demo/run_physical.py`, `demo/physical_topology.py`, `demo/physical_node.py`, `agentic/benchmark/physical.py`, `deploy/pi/**`, `demo/generate_physical_report.py`, tests. Detailed in §5.

**AC-PR4:** §5.7.

**Does NOT absorb:** D's sweep/observer (PR-3), producer wiring (PR-1), demux fix (PR-2).

### PR ordering rule

PR-1 and PR-2 are independent; in practice **PR-1 then PR-2** (PR-2 is a one-clause deletion that must not be entangled with PR-1's multi-file wiring). PR-3 requires both. PR-4 requires PR-3. No PR may absorb another's concern. The `kind`/namespace isolation (§6.4) keeps D and E in separate result streams even though PR-4 reuses PR-3's observer.

---

## 3. PR-1 — G.3 producer wiring design

### 3.0 The spike must precede everything (NF-3, mandatory)

The response path rests on two read-verified-but-runtime-unproven facts (§0.1): (i) `[face_id, Interest]` reaches `data_from_lower` with `data[0]` = the requester's face, and (ii) an injected `Content` down the forwarder stack turns into `Outbound("lower", [requester_face, content])`. HF-3 makes proving both **the first deliverable of PR-1**. Order of work:

```
SPIKE (assertions a & b)  ──PASS──►  producer translation  ──►  response seam  ──►  hardening
        │
        └──FAIL──►  STOP. Do not touch any D/E file. Record which assertion failed and
                    whether the stack does not preserve the reply face (then PR-1 must add
                    an explicit reply-face plumbing path before proceeding).
```

The spike is a standalone test file (`agentic/tests/test_producer_path_spike.py`) using a real `AgenticForwarder` on a `SimulationBus`. It touches **only** the forwarder stack; no D/E file is created until it is green. §0.2 item 1 flags it as the top open risk until then.

### 3.1 The topology D will use **after** PR-1

```
                 SimulationBus (async)
  ┌──────────────────────────┐         ┌───────────────────────────────────────────┐
  │ INTAKE / AMBULANCE        │         │ EDGE (AgenticForwarder, runtime=async)      │
  │  AgenticLayer             │         │  AgenticLayer (top of stack)                │
  │   .submit_intent(...)     │         │    register_capability(desc, LatencyBackend │
  │     → serial|concurrent   │         │        (DeterministicBackend(handler)))     │
  │       send_request(name)  │  /cap/fwd/… │  data_from_lower: [face_id, Interest] →  │
  │  PicnSubstratePort        │ ───────►│    InboundRequest → _on_inbound_request →   │
  │   _outstanding[corr]=name │         │    hub.invoke → backend (sleeps latency_s)  │
  │   _deliver_uplink_packet  │ ◄───────│  send_response → Content down forwarder     │
  │   ← ResponseArrived       │ Content │    (Case A only) → PIT face → requester     │
  │  RecordingLatencyObserver │         │  RecordingLatencyObserver (producer side)   │
  └──────────────────────────┘         └───────────────────────────────────────────┘
```

- **Intake:** `AgenticLayer` + `PicnSubstratePort(edge_addr, None, interfaces=[bus.add_interface(amb_addr)])`, as `cardiac_bus_topology.py:257-264` builds it.
- **Edge:** `AgenticForwarder(port=0, encoder=NdnTlvEncoder(), interfaces=[bus.add_interface(edge_addr)], runtime=Runtime.ASYNC)`, but **instead of** `_configure_edge_cs` preloading Content into the CS, the edge calls **`edge.register_capability(descriptor, LatencyBackend(DeterministicBackend(handler, …), latency_s=cfg.leaf_latency_s))`** for each capability prefix. This is the change that makes `hub.invoke` reachable.
- **`LatencyBackend` is invoked inside `hub.invoke`** (`layer.py:198-200` → `producer.py:91-93` → registry → `LatencyBackend.invoke`), so `T_service` is real.

### 3.2 Exact code to build (description only — CRAFT implements)

#### 3.2a Interest → `InboundRequest` translation (producer side — the dead-code fix)

**Location (v3-corrected, v4-confirmed):** `AgenticLayer.data_from_lower` (`layer.py:315-328`), replacing the no-op. Not the adapter (v3 §0 Correction 1).

**NF-2 — exact accepted input shapes and contract.** `data_from_lower` **must** accept both of the following, and must **not** assume a bare `Interest`:

1. **Canonical shape (the production path): `[face_id, Interest]`** — a 2-element list whose `[0]` is an `int` face id and `[1]` is a `PiCN.Packets.Interest`. This is exactly what `ICNLayerCore.py:120-124` puts on `queue_to_higher` and what NFN passes through unchanged (`NFNLayerCore.py:117-123`). The face id **is** the reply face; it must be preserved (NF-2).
2. **Typed-`Outbound` inner-item variant:** the same semantic payload delivered as `(face_id, interest)` where the item arrived via an `Outbound` whose `.item` is the list — i.e. the method must be robust to the item being the `[face_id, Interest]` list itself, not an `Outbound` wrapper. (The wrappers already unwrap `Outbound`; this clause exists so a direct unit-test harness that builds the shape by hand cannot diverge from production. The method treats a 2-tuple and a 2-list identically.)

Contract (stated so CRAFT cannot mis-implement):
- If `data` is not a 2-element sequence, or `data[0]` is not an `int`, or `data[1]` is not an `Interest` **and** not a `Content`/`Nack`, the method **returns without raising** (a layer must not crash on foreign traffic).
- For an `Interest`: derive `correlation` deterministically from the Interest's name (no wire token; see §3.2c), construct an `InboundRequest(correlation=…, name=…, payload=b"", at=…)`, carry the reply face, and **enqueue onto the layer's own `self._inbound` queue** so `_dispatch_event` (`layer.py:167-177`) routes it to `_on_inbound_request`. It does **not** call `_on_inbound_request` directly (that would bypass the single event pump and reorder under concurrency).
- For a `Content` (should it ever arrive here): ignore at this layer (the forwarder's ICN resolves Content; it does not belong at the top).
- The name is converted using `PiCN.Packets` name access directly (AC2-permitted); **`layer.py` must not import the adapter** (`agentic.adapters.picn`).

**NF-4 — reply-face carriage: Option 2 chosen (side table), justified.** `InboundRequest` (`events.py:83-96`) is part of the **closed union** whose own docstring forbids extension without updating every adapter and dispatcher (`events.py:99`). Option 1 (add `reply_ref` to `InboundRequest`) would force, in one commit: an edit to the union comment, every constructor (`port.py:215-226`, `mock/port.py:138-159`), and a round-trip test through both adapters — real churn on a deliberately-frozen contract for a field that is **meaningless to the mock** (the mock's "reply" is `send_response` recording, not a face). Option 2 keeps the union frozen and localizes the new state to the layer that owns it:

- `AgenticLayer` gains `self._inbound_reply_ref: dict[bytes, int]`, keyed by the inbound `correlation`.
- On translation, `self._inbound_reply_ref[correlation] = face_id`.
- `_on_inbound_request` reads `self._inbound_reply_ref.pop(event.correlation, None)` and passes it to the response seam. Unknown/absent ref → the seam drops with a logged warning (never raises).
- Lifecycle: the entry is popped when the response is sent; a periodic sweep is **not** required because one inbound request produces exactly one response in this design (bounded by `MAX_CONTEXT_PIT_ENTRIES`-style usage; the dict is emptied on `stop_port`).

Justification: keeps the closed-union invariant intact (NF-4's stated risk), avoids touching both adapters' constructors and the mock for a field the mock cannot use, and confines new state to the layer. The cost (a small dict) is bounded and testable. If Dennis later wants the field on the event, it is Option 1's one-commit change; v4 does not do it.

#### 3.2b `send_response` builds a `Content` and transmits it (NF-1 fix)

**NF-1 — the v3 "Case B" is deleted as a transmission path.** v3 §3.2b Case B told CRAFT to push `Content` onto `self._lstack.queue_from_higher` for `PicnSubstratePort`. That is **unroutable as written**: `PicnSubstratePort._lstack` is `[chunk, timeoutprevention, packetencoding, link]` (`port.py:91-98`) — **there is no ICN layer**, so there is no PIT/FIB to resolve a return face. A Content placed there serialises straight onto the wire with no forwarding decision. **Case B is removed.**

The production response path is **Case A only** — forwarder/ICN-backed. Two wiring facts make Case A correct:
- `_on_inbound_request` runs inside the producer edge's `AgenticLayer`, which sits at the top of the `AgenticForwarder` stack (`AgenticForwarder.py:142-152`). Its `queue_to_lower` **is** NFN's `queue_from_higher` (`AsyncLayerStack.py:62-71`).
- Pushing a `Content` down that queue traverses NFN → chunk → timeoutprevention → ICN; at ICN `data_from_higher` → `handle_from_higher` → `handle_content` with `from_local=True, has_to_higher=True` (`ICNLayerCore.py:40-41`), finds the inbound PIT entry, and (because that face's `local_app=False`, §0.1 step 4) emits `Outbound("lower", [requester_face, content])` (`ICNLayerCore.py:150-154`).

**The forwarder-backed response seam (specified exactly — NF-1).** PR-1 introduces a response seam so `AgenticLayer` never reaches into adapter internals:

- Add to `AgenticLayer` a response hook, e.g. `async def _transmit_response(self, *, reply_ref: int, name: Name, payload: bytes) -> None`.
- The hook builds `Content(to_picn_name(name), payload)` and awaits `self.queue_to_lower.put([reply_ref, content])`. `queue_to_lower` is the layer's own instance queue (set by the stack, `AsyncLayerStack.py:68`), so this is a plain layer-to-lower push, **not** an adapter call.
- `_on_inbound_request` calls the hook instead of `self._port.send_response(...)` **when the node is forwarder-backed** (i.e. `queue_to_lower` is attached). The existing `self._port.send_response(...)` remains for the **mock/unit-test path** where `AgenticLayer` is driven by `MockSubstratePort` (which records via `_responses_sent`, `mock/port.py:275-283`). The two are mutually exclusive per node configuration and the layer selects by whether a forwarder-backed seam is installed.

**Unit-test seam (specified exactly — NF-1).** For `PicnSubstratePort` unit tests, do **not** reuse the client `_lstack` (it has no ICN layer). Instead, `PicnSubstratePort` gains a **forwarder-backed response seam** that is a tiny injectable callable `response_sink: Callable[[bytes, bytes], Awaitable[None]] | None = None` (constructor kwarg, default `None`). When set, `send_response` delegates to it; when `None`, it keeps the existing `_response_payloads[correlation] = payload` behaviour (`port.py:212-213`) for the mock-only path. Tests inject a sink that records the `(correlation, payload)` pair and asserts the `Content` the seam would have produced. This keeps `PicnSubstratePort` itself free of ICN internals and makes the seam's contract testable without a live forwarder. Production wiring never uses the sink; the forwarder-backing is what supplies the real path.

**Delete the v2/v3 "no adapter change" claim.** PR-1 changes `port.py`, `layer.py`, and `AgenticForwarder` (and the topology file in PR-3).

#### 3.2c Correlation plumbing (no wire token — Option 3)

- **Requester side:** `send_request` registers `self._outstanding[correlation] = name` **before** any `await` (`port.py:187`). Unchanged.
- **Producer side (no token, Option 3):** since the wire carries no token, the producer's inbound `correlation` is derived from the **Interest's name** (e.g. `sha256("inbound:" + name bytes)`), and the response `Content` is named by the **same Interest name**. The requester's `_match_outstanding` then matches by **directional LPM** (PR-2) — correct for concrete names.
- **Stated limitation:** under aggregation/wildcards there is no name-independent key; this is the wire-format limitation (§7, A-012). PR-1 must not pretend otherwise. The producer `correlation` is a **local** key for the reply-ref table; it is not compared to the requester's correlation.

#### 3.2d Correlation-carrying Nack OR documented limitation

- Today the Nack demux picks the oldest outstanding by dict insertion order (`port.py:296-311`), arbitrary under concurrency. PR-1 takes the **preferred path**: the Nack path records the **Interest name** it carried (`Nack` carries `name`; `decode_nack` returns `(name, reason)`, `NdnTlvEncoder.py:294-313`) and `_match_outstanding` is used **on the Nack name** to find the correlation (directional LPM). Fall back to **dropping** the Nack when no match, rather than mis-attributing. This stays within the no-token design.
- Minimum acceptable fallback (if the preferred path proves unstable): keep oldest-outstanding but **document and test** the limitation, `test_nack_demux_ambiguous_is_documented`. ARCO requires one or the other; take the preferred path.

#### 3.2e Idempotent late/duplicate response

**Location:** `AgenticLayer._on_response` (`layer.py:213-220`) and `_on_failed` (`layer.py:230-237`).
- Before `record_response`, check the entry: if `entry is None` or `entry.terminated`, **drop** (return) without raising. Mirrors `maybe_complete`'s idempotency (`aggregation.py:146-147`).
- A test injects a second `ResponseArrived` for the same correlation after completion and asserts **no exception and unchanged trace root**.
- `ContextPIT.record_response` keeps raising on terminated entries (`context_pit.py:202-204`) — the **guard moves to the layer**, which is the correct owner (the PIT is a strict state machine). Document this split.

#### 3.2f Deterministic TaskGroup failure semantics

**Location:** `submit_intent` concurrent branch (PR-3) + this helper contract.
- Normal substrate failure already arrives as `RequestFailed` → `_on_failed` → `record_response(NULL)` (`layer.py:230-237`).
- A **cancelled/errored leaf task** must still emit a NULL. Design: each leaf coroutine catches **its own** exceptions and cancellation, records a NULL via a new `AgenticLayer._record_leaf_failure(parent, leaf_index)` helper, and returns; the `TaskGroup` then sees no child exception in normal operation. A residual `ExceptionGroup` is unwrapped (ADR-007 discipline, consistent with `AsyncLayerProcess.py:95-107`) and re-raised **after** NULL recording.
- **Invariant:** `done` always resolves. Test: kill one leaf mid-flight (mock port raising/cancelling) → `submit_intent` returns a trace root with that leaf = `NULL_RESPONSE`, no hang.

### 3.3 Verification (PR-1)

New tests under `agentic/tests/`:

| Test | Asserts |
|---|---|
| `test_producer_path_spike.py` | **(gate, first)** assertion (a) `[face_id, Interest]` reaches `data_from_lower` with `data[0]` = requester face; assertion (b) injected `Content` down the forwarder stack → `Outbound("lower", [requester_face, content])` |
| `test_producer_path_live_roundtrip.py` | SimulationBus: ambulance submit_intent → edge registered producer → `hub.invoke` **called** (spy) → Content returns → trace root verified; returns within timeout |
| `test_inbound_request_translation.py` | `data_from_lower` receives `[face_id, Interest]`, emits `InboundRequest` with reply ref preserved (`== data[0]`); malformed input returns without raising |
| `test_send_response_content.py` | `send_response` with the forwarder-backed seam yields a `Content` on the wire; requester port demuxes to the right correlation |
| `test_late_response_idempotent.py` | duplicate/late `ResponseArrived` after termination dropped, no `ValueError`, root unchanged |
| `test_nack_correlation.py` | Nack with a name correlates via LPM; no match → drop, not mis-attribute |
| `test_taskgroup_failure_semantics.py` | one leaf fails/cancels → NULL at that leaf, `done` resolves, root deterministic |
| `test_submit_intent_serial_golden.py` | serial path unchanged (root + dispatch count identical to pre-PR-1) |

Run (spike first, then the rest): `python -m pytest agentic/tests/test_producer_path_spike.py -v && python -m pytest agentic/tests/test_producer_path_live_roundtrip.py agentic/tests/test_inbound_request_translation.py agentic/tests/test_send_response_content.py agentic/tests/test_late_response_idempotent.py agentic/tests/test_nack_correlation.py agentic/tests/test_taskgroup_failure_semantics.py agentic/tests/test_submit_intent_serial_golden.py -v`

---

## 4. Experiment D v4 — Concurrent fan-out + T_intent decomposition (real producer path)

### 4.0 Scope

Two fan-out paths exist and are different code:
1. `AgenticLayer.submit_intent` (`layer.py:295-313`) — the real forwarding path over a `SubstratePort`. **D's primary target.**
2. `CardiacScenario._run_exchange` — structural in-process path bypassing the port. **Not modified for concurrency.**

D's edge is an `AgenticForwarder` with **registered producers** (PR-1), so each leaf actually reaches `hub.invoke` and `LatencyBackend` runs. This is what makes `T_service` real and `concurrent_publishable` reachable.

### 4.1 Concurrency mechanism + prepass

- **Mechanism: `asyncio.TaskGroup`** with the PR-1 failure semantics (§3.2f): any failed/cancelled leaf emits NULL, `done` always resolves. No hang.
- **Prepass (required):** before dispatch, for every leaf precompute `correlation`, register `self._leaf_index[correlation] = (parent, leaf_index)`, call `self._pit.mark_forwarded(...)`, and build the wire name. A fast response must not race an unpopulated mapping (`_on_response` drops on `mapping is None`). The prepass makes this impossible by construction.
- **I2 preserved:** commit (`layer.py:284-290`) is synchronous and precedes the dispatch block. No `await` between commit and first dispatch. D adds a test asserting commit precedes all `RequestSent` events.
- **Index-keyed Merkle:** completion order never enters the digest (`context_pit.py:207`; `layer.py:213-237`). Out-of-order test retained (`MockSubstratePort(hold_terminals=True)` + reversed `release_terminals`, `mock/port.py:161-174`).
- Default `dispatch="serial"` preserves existing behaviour bit-for-bit.

### 4.2 `LatencyObserver` — sourcing with the now-real producer path

Placement: `agentic/agentic_layer/observer.py` (new; imports only stdlib + `agentic.port.events`; no `PiCN.*`, no LLM). Injected as `observer: LatencyObserver | None = None`; default `NullLatencyObserver`. `layer.py` calls hooks only; it never emits `MetricEvent`s and never computes derived metrics.

| Symbol | Source | Clock domain |
|---|---|---|
| `t0` (commit) | `time.perf_counter()` at `on_commit`, immediately after `self._pit.commit(...)` returns (`layer.py:290`) | intake monotonic |
| `t_send` | `RequestSent.at` (`port.py:188-190`), captured in `_dispatch_event` (`layer.py:176-177`) | port clock |
| `t_response` | `ResponseArrived.at` (`port.py:287-294`), captured in `_on_response` (`layer.py:213`) | port clock |
| `t_service` | **Producer side, around `self._hub.invoke(...)` in `_on_inbound_request` (`layer.py:198-200`).** PR-1 makes the producer path reachable and D registers a `LatencyBackend`, so this is a **real, non-zero measurement** | producer clock |
| `t1` (dispatch end) | `time.perf_counter()` at `on_dispatch_end` after the dispatch block | intake monotonic |
| `t_agg` | `time.perf_counter()` at `on_aggregate`, called from `_maybe_finish` after `maybe_complete` returns (`layer.py:239-245`) | intake monotonic |
| `t_intent` | `time.perf_counter()` at `on_complete` when `done` resolves (`layer.py:313`) | intake monotonic |

**Observer interface (exact method set):** `on_commit(*, parent, t_commit, leaf_count)`, `on_dispatch_begin(*, parent, t0)`, `on_leaf_forwarded(*, correlation, parent, leaf_index, name, t_send)`, `on_leaf_response(*, correlation, parent, leaf_index, t_response)`, `on_dispatch_end(*, parent, t1)`, `on_aggregate(*, parent, t_aggregate, trace_root)`, `on_complete(*, parent, t_intent)`.

**`t_emission` — caller-threaded (Finding 10).** `t_emission` is not observable inside `submit_intent`. Add an optional `emission_ts: float | None = None` parameter; the D runner captures `t_emission = time.perf_counter()` immediately before the call and passes it. If absent, `T_decompose` is **not reported** (`t_decompose_ms` omitted), never mis-sourced. `T_decompose` includes caller→layer call overhead definitionally.

**`T_dispatch` differs by mode definitionally.** Serial `T_dispatch ≈ k·L`; concurrent `T_dispatch ≈ L + overhead`. This is the measurement, not an artifact. Documented in the report.

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
- **D honesty:** `T_network` remains **near-zero by construction** (SimulationBus); `T_service` is **real and non-zero** because the producer backend is invoked via PR-1. Therefore View B is no longer vacuous. If, after PR-1, D's `T_network` is unmeasurable, D publishes a **four-way** decomposition with `T_network` explicitly marked `not_measurable` (gate §4.5 cond. 7). No component is published as measured when it is not.

### 4.3 Metric schema deltas v4 (D)

`MetricEvent(kind, transport, seed, value, labels)` frozen; all new dimensions in `labels`.

**New `EventKind` values** (append to `events.py:14-26`):
```
"t_intent_ms", "t_decompose_ms", "t_dispatch_ms", "t_network_ms",
"t_service_ms", "t_aggregate_ms", "t_dispatch_residual_ms",
"fanout_serial_ms", "fanout_concurrent_ms", "fanout_speedup",
"leaf_inflight_peak", "leaf_overlap_fraction", "leaf_start_ms", "leaf_end_ms",
"observer_overhead_ms",
```
**`config_snapshot` is NOT an `EventKind`** — it moves to `RunMetadata.parameters["config_snapshot"]` (Finding 11).

**`MetricsSnapshot` additive fields** (`metrics.py:31-60`, `None` defaults): `concurrent_publishable`, `fanout_serial_ms`, `fanout_concurrent_ms`, `fanout_speedup`, `leaf_inflight_peak`, `leaf_overlap_fraction`, `t_intent_ms`, `t_decompose_ms`, `t_dispatch_ms`, `t_network_ms`, `t_service_ms`, `t_aggregate_ms`, `t_dispatch_residual_ms`, `observer_overhead_ms`, `physical_publishable`.

Paper aliases: `concurrent_dispatch_publishable` → `concurrent_publishable`; `physical_deployment_publishable` → `physical_publishable`; `t_intent_total_ms` → `t_intent_ms`.

`compute_metrics` gains only an additive tail block; existing event sets produce byte-identical outputs. D aggregation lives in new `agentic/benchmark/concurrency.py`.

### 4.4 Sweep specification

- `--concurrency {serial,concurrent}` — default both (paired).
- `--leaf-latency-s {0,0.01,0.05,0.1,0.5}`.
- `--k 2,3,4,5,8`.
- `--seeds 1-5`.
- `--path happy`; `--transport bus`.
- **Control cell:** `leaf_latency_s = 0` in every campaign; expected ~1.0×, bound to `control=true` (gate §4.5).
- The **zero-latency control is not run in LLM cells** (Finding 9) — redundant there; E has LLM, D does not.

**Paired-run semantics:** one record per `(seed, k, leaf_latency_s)` with both modes' times, decompositions, speedup. Valid iff both modes completed with identical `k` and byte-identical trace roots.

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

### 4.5 Publishability gate `concurrent_publishable`

> `concurrent_publishable` is `True` **iff all** hold for a cell:
> 1. Both `fanout_serial_ms` and `fanout_concurrent_ms` exist for the **same** `(transport="bus", seed, k, leaf_latency_s)`.
> 2. **`control` is structurally bound to `leaf_latency_s`:** the record's `labels["control"]` must equal `(leaf_latency_s == 0.0)`. The gate **recomputes** `control` from `leaf_latency_s` and refuses any record where the stored flag disagrees. A `leaf_latency_s=0` record can never satisfy conditions 3–8; a `leaf_latency_s>0` record with `control=true` is rejected as malformed.
> 3. `leaf_latency_s > 0` **and** `control is False`.
> 4. Same leaf set: identical `k` and identical recorded `expected_subintent_set` digests.
> 5. `trace_root_equal is True`.
> 6. `fanout_speedup` finite and `> 0`.
> 7. **`T_service` present and non-zero** (`t_service_ms` exists, `labels["measured"] == true`, value `> 0`), OR the record explicitly marks `t_service_ms` as `not_measurable` **and** publishes only the four-way decomposition (no View-B attribution claim). A record presenting a five-way decomposition with a zero/unmeasured `T_service` fails the gate.
> 8. `labels["T_intent_view"] ∈ {"A"}` or explicit `T_dispatch_residual_ms` present.
>
> Failing any → `concurrent_publishable=False`; the figure layer refuses to render the speedup curve.

**Namespace test mechanism (precise).** `demo/generate_concurrency_report.py` consumes **exactly** `{"concurrency_run"}`; `demo/generate_physical_report.py` consumes **exactly** `{"physical_run"}` (E). Enforced by an **AST/import check** that parses each generator with `ast`, collects string literals in `MetricEvent.kind == …` / record-`kind` comparisons, and asserts the exact set. Static check on the generator's own source; no `PiCN.*` import.

### 4.6 File plan (Experiment D v4)

| File | Action | Purpose |
|---|---|---|
| `agentic/binding/latency.py` | new | `LatencyBackend` wrapper (seeded jitter optional) |
| `agentic/binding/__init__.py` | modify | export `LatencyBackend` |
| `agentic/agentic_layer/observer.py` | new | `LatencyObserver` + `Null` + `Recording` |
| `agentic/agentic_layer/dispatch.py` | new | `DispatchMode` enum |
| `agentic/agentic_layer/layer.py` | modify | `dispatch` param; prepass; TaskGroup; observer hooks; `emission_ts`; `data_from_lower` producer translation (PR-1); reply-ref side table (PR-1); forwarder-backed response seam (PR-1); idempotent `_on_response`/`_on_failed` (PR-1) |
| `agentic/port/events.py` | modify (comment only) | NF-4 decision recorded as a comment; **union unchanged** |
| `agentic/benchmark/events.py` | modify | append kinds (no `config_snapshot`) |
| `agentic/benchmark/concurrency.py` | new | D snapshot, speedup, gate |
| `agentic/benchmark/metrics.py` | modify | optional fields + aliases |
| `agentic/benchmark/__init__.py` | modify | exports |
| `agentic/adapters/picn/port.py` | modify | **PR-2** reverse clause deletion; **PR-1** response sink seam |
| `demo/run_concurrency.py` | new | D runner |
| `demo/concurrency_topology.py` | new | D wiring: ambulance + edge with registered producers (PR-1) |
| `demo/generate_concurrency_report.py` | new | speedup-vs-k + stacked bars + honest captions |
| `agentic/tests/test_concurrency_paired.py` | new | paired same-leaf-set root equality, speedup, peak, overlap |
| `agentic/tests/test_latency_backend.py` | new | determinism, jitter seeding, conformance |
| `agentic/tests/test_latency_observer.py` | new | component definitions, single-clock, Null no-op |
| `agentic/tests/test_concurrent_publishable.py` | new | gate incl. control↔leaf_latency binding, T_service presence |
| `agentic/tests/test_submit_intent_i2_concurrent.py` | new | out-of-order via `hold_terminals`+reversed release |
| `agentic/tests/test_name_correlation.py` | new | **PR-2** prefix trap |

**Import discipline:** `layer.py` imports no `PiCN.*` beyond `PiCN.Processes`/`PiCN.Packets` (AC2); `observer.py`/`dispatch.py` import no PiCN.

### 4.7 Acceptance criteria (AC-PR3)

1. `python -m pytest agentic/tests/test_submit_intent_serial_golden.py -v` — `dispatch="serial"` default; golden unchanged.
2. `python -m pytest agentic/tests/test_concurrency_paired.py -v` — paired serial/concurrent same-leaf-set → byte-identical trace root; speedup > 1 with `leaf_latency_s > 0`; ~1.0× at `leaf_latency_s = 0`.
3. `python -m pytest agentic/tests/test_submit_intent_i2_concurrent.py -v` — out-of-order completion → correct root.
4. `python -m pytest agentic/tests/test_concurrent_publishable.py -v` — gate §4.5 green on a real paired cell; red on: unpaired, zero-latency-as-speedup, differing root, `control`/`leaf_latency_s` mismatch, zero `T_service` presented as five-way.
5. `python -m pytest agentic/tests/test_producer_path_live_roundtrip.py -v` — `hub.invoke` invoked at the edge; `T_service > 0`.
6. `python -m pytest agentic/tests/test_report_namespace_isolation.py -v` — AST per-generator kind check.

---

## 5. Experiment E v4 — Physical testbed (≥2 edges, UDP, staged LLM)

### 5.1 Topology — `--edges N` (first campaign N=2)

E depends on PR-1 so producers are **registered capabilities**, not CS preloads.

- **Intake:** `AgenticLayer` + **one `PicnSubstratePort` per edge** + `RecordingLatencyObserver`.
- **Edges:** `AgenticForwarder` with `register_capability` for the capability prefixes it owns.
- **Producers:** registered backends (`DeterministicBackend`, `LatencyBackend`, `PydanticAIBackend`).
- **Capability-prefix ownership:** edge-1 owns `/cap/fwd/hospital/beds/h1..h4`, edge-2 owns `h5..h8`. The intake fans out over one port per edge; each port uses `peer_host=<edge IP>, peer_port=<edge UDP port>` (`port.py:99-107`).
- **Routing decision** at the intake is a true **directional LPM** over the registered prefix table (`longest_prefix_match`, `port.py:167-175`), the paper's demonstrated mechanism, matching PR-2.
- **N=1 single-edge** remains a supported control cell.
- **Distributed Context PIT stated honestly:** in-memory, node-local; the intake holds the authoritative Context PIT and aggregates; the Merkle root is computed at the intake. Not claimed: cross-node replication/consensus/distributed root.

### 5.2 Clock-offset correction

Intake-side timestamps single-clock; producer-side `t_service` offset-corrected via NTP/chrony measurement before each campaign; residual skew recorded; runs above threshold (default 1 ms) excluded and counted. `T_network` needs no cross-clock (both ends intake events).

### 5.3 LLM staging + cost matrix (Finding 9)

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
- **`--leaf-latency-s` is dropped from LLM cells** (Finding 9): inference dominates.
- **TOML contract** (`pydantic_ai_backend.py:32-55`, local-first):
  ```toml
  [model]
  preference = ["ollama:llama3.2", "openai:gpt-4o-mini"]
  ```
- **Per-leaf split:** `inference_ms` (around `backend.invoke` at producer), `transport_ms` (intake-clock `t_response − t_send`), `total_ms`. Inference is never credited to the overlay.

**Cost matrix (explicit, per campaign).** Let `D_cell` = per-run wall-time dominated by LLM inference (measured in Stage A as `T_intent`), `n` = runs/cell, `C` = cells.

| Campaign | cells `C` | runs/cell `n` | est. per-run | est. total (serial) |
|---|---|---|---|---|
| E-DET (`--backend deterministic`) | `seeds 5 × k 8 × edges 1 = 5` | 30 | < 1 s | ~2.5 min |
| E-LLM-A (on-Pi) | `5 × 1 × 2 placements = 10` | **30** | 3–30 s (model-dependent) | **25 min – 2.5 h** |
| E-LLM-C (off-Pi) | 10 | **30** | 1–10 s | 8 min – 50 min |

**Decision and justification.** `n=30` is retained for **deterministic** cells (cheap). For **LLM** cells `n` is **reduced to the minimum defensible** with disclosure:
- Default LLM `n = 15` (report `n`, median, mean, stdev, and a bootstrap CI; state in the caption that LLM cells use `n=15` because per-run cost is inference-dominated and the substrate share is the quantity of interest).
- The gate requires `n ≥ MIN_LLM_RUNS (=15)` for LLM cells and `n ≥ MIN_PHYSICAL_RUNS (=30)` for deterministic cells, each disclosed in the record.
- A `--runs-per-cell` override is allowed upward; a **cost preflight** (dry-run: one warm run per cell) prints the projected wall-clock and **refuses to start** a campaign whose projection exceeds `--max-hours` (default 6 h) unless `--ack-cost` is passed (A-011 style — refuse loudly).

### 5.4 `physical_publishable` gate (NF-5 resolved)

> `physical_publishable` is `True` **iff**:
> 1. `metadata.transport == "udp"`; `--edges >= 1` recorded; **per-node run markers** recorded for every node.
> 2. **Deployed-commit attestation:** every node's run marker records the **`PICN_COMMIT` actually deployed and the commit on disk on that node** at run time, verified equal. A clean local tree is **not** sufficient.
> 3. Intake `submit_intent` completed with `trace_root_verified is True`.
> 4. Per-cell run count meets the type-specific threshold: deterministic cells `n ≥ 30`, LLM cells `n ≥ 15`, same `(seed, k, backend, llm_placement, edges)`; report `n / median / mean / stdev`.
> 5. All reported `T_intent` components present per run (see condition 7 for the unmeasurable-component rule); for `backend="llm"`, the `inference_ms`/`transport_ms` split present per leaf.
> 6. Host clock skew recorded and below threshold; runs above threshold excluded and counted.
> 7. **Name-based routing proof:** at least one leaf is answered by a **producer whose declared capability prefix is owned by a non-intake node, and that ownership is recorded in that node's per-node run marker**. The gate cross-checks the answering producer's node marker's `owned_prefixes` and `deployed_commit` — a leaf answered locally, or by a producer not attested as owning that prefix, does not satisfy it.
> 8. **Multi-edge scoping (NF-5):** for **deterministic** multi-edge cells, ≥ 2 edges must have served at least one leaf each (recorded per-edge leaf counts). For **LLM** cells, condition 8 is **not** required — the LLM cells' claim is substrate-share under inference, not multi-edge fan-out breadth; their state is recorded as `multi_edge_breadth_required=false` and the report must caption the LLM cell as a single-edge/shared-edge measurement.
>
> Failing any → gate `False`; figures refuse to render; record retained for audit.

**NF-5 gate rationale (explicit, so the paper's multi-edge claim is correctly scoped).** Condition 7 only requires **one** non-intake-owned leaf, so on its own it could pass with a single non-intake leaf — it proves *name-based ownership-based routing*, not fan-out breadth. Condition 8 supplies the breadth requirement, and NF-5 makes its **scope** explicit:
- `physical_publishable` requires **≥ 2 edges serving ≥ 1 leaf** **only for deterministic multi-edge cells**.
- **LLM cells** are exempt from condition 8 and instead record `multi_edge_breadth_required=false`; their multi-edge claim is therefore **not** made. The paper must caption any LLM cell accordingly and must not attribute a multi-edge fan-out claim to an LLM cell that did not satisfy condition 8.
- The multi-edge fan-out claim in the paper is scoped to **deterministic** cells only.

### 5.5 Honest framing discipline (tiers)

| | Experiment D | Experiment E |
|---|---|---|
| Transport label | `bus` | `udp` |
| Nature | SimulationBus **mechanism illustration** | **physical deployment** |
| Backend | `DeterministicBackend` only | `DeterministicBackend` (+ optional `PydanticAIBackend`) |
| T_network | **near-zero by construction** | **real, measurable** |
| Gate | `concurrent_publishable` | `physical_publishable` |
| JSONL namespace | `demo/results/concurrency.jsonl` | `demo/results/physical/<run-id>.jsonl` |
| Kind tag | `kind="concurrency_run"` | `kind="physical_run"` |

**HARD RULE:** D and E numbers **never** share a table, figure, or derived ratio. Enforced by the §4.5 AST namespace test.

**Caption rules (mandatory):**
- Every D caption: *"SimulationBus mechanism illustration on a single host; deterministic backends; T_network is near-zero by construction; not a deployment measurement."*
- Every E caption: *"Physical deployment on N Raspberry Pi 5 nodes over UDP at commit `<sha>`; measured wall-clock includes real transport and (for the LLM variant) model inference, reported separately; T_intent decomposed with producer-side service time clock-offset-corrected."*
- **Forbidden:** citing a D speedup/T_network in an E claim or vice versa; calling D a "deployment"; calling E "controlled"/"comparable to D"; collapsing D+E into one number; plotting D and E on shared axes without explicit tier separation.

### 5.6 Provisioning structure, env contract, bring-up

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
    bringup.md, teardown.md, clock_sync.md
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
min_llm_runs=15
```

**Env contract (per node):** `PICN_ROLE`, `PICN_EDGE_ID`, `PICN_LISTEN_PORT`, `PICN_PEER_HOST`, `PICN_PEER_PORT`, `PICN_OWNED_PREFIXES`, `PICN_BACKEND`, `PICN_LLM_PLACEMENT`, `PICN_MODEL_CONFIG`, `PICN_RESULTS_DIR`, `PICN_RUN_ID`, `PICN_SEED`, `PICN_K`, `PICN_LEAF_LATENCY_S`, `PICN_EDGES`.

**Per-node run markers** written to `PICN_RESULTS_DIR/markers/<run_id>-<host>.json` containing `{host, role, deployed_commit, disk_commit, owned_prefixes, started_at, ended_at}`. Conditions 1/2/7/8 consume these.

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

### 5.7 Acceptance criteria (AC-PR4)

1. `python -m pytest agentic/tests/test_physical_gate.py -v` — E-DET campaign completes; `physical_publishable=True` on a valid deterministic multi-edge cell; gate red on: `< 2` eligible edges for a deterministic multi-edge cell, missing/mismatched deployed-commit marker, leaf answered by a node not owning the prefix, LLM cell `n < 15`, skew above threshold; LLM cell exempt from condition 8 and records `multi_edge_breadth_required=false`.
2. `python -m pytest agentic/tests/test_run_physical_cli.py -v` — CLI contract; namespace isolation; cost preflight prints projected wall-clock.
3. `python -m demo.run_physical --backend llm --llm-placement on-pi --edges 2 --seeds 1 --k 8 --runs-per-cell 1 --dry-run --max-hours 0` exits non-zero with the projection unless `--ack-cost` is passed (preflight enforcement).
4. `python -m pytest agentic/tests/test_report_namespace_isolation.py -v` — AST namespace test green.

### 5.8 File plan (Experiment E v4)

| File | Action | Purpose |
|---|---|---|
| `demo/run_physical.py` | new | E runner CLI: cost preflight, `--max-hours`, `--ack-cost`, `--dry-run` |
| `demo/physical_topology.py` | new | E wiring: intake `AgenticLayer` + one `PicnSubstratePort` per edge; registered producers |
| `demo/physical_node.py` | new | Per-node entrypoint via `PICN_ROLE`; writes per-node run markers; systemd-friendly |
| `demo/collect_service_records.py` | new | Gather producer-side observer records; apply clock-offset correction |
| `agentic/benchmark/physical.py` | new | E snapshot + `physical_publishable` gate (pure Python, no `PiCN.*` → AC1 safe) |
| `deploy/pi/**` | new | inventory, playbook, roles, group_vars/host_vars, runbooks, `model.toml` |
| `demo/generate_physical_report.py` | new | E report + captions; reads only `kind="physical_run"` |
| `agentic/tests/test_physical_gate.py` | new | all gate conditions incl. deployed-commit, prefix-ownership, NF-5 scoping |
| `agentic/tests/test_run_physical_cli.py` | new | CLI contract, namespace, preflight |
| `demo/README.md`, `docs/agentic_demo.md` | modify | document E, distinguish from D, NF-5 scoping |

---

## 6. Metric schema evolution v4 (backward-compatible deltas)

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

Three gates are independent booleans; no gate implies another. Report generators check the gate matching their figure. `kind`/namespace isolation is enforced by the §4.5 AST test.

### 6.5 `RunMetadata` delta
`parameters["config_snapshot"]` (one per campaign) and `parameters["deployment"]` (E: `{picn_commit, run_id, edges, per_node_markers}`). No `EventKind` change.

---

## 7. Honesty & anti-strawman checklist v4

**WIRE-FORMAT LIMITATION (mandatory — Dennis; appears here, in A-012, and in the paper):**

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
- [ ] Observer overhead bounded by calibration (`--observer on/off`); reported.
- [ ] Out-of-order completion exercised; root verified.
- [ ] Peak in-flight/overlap measured; flattening at large k reported.

**Experiment E:**
- [ ] `transport=udp`; no bus in E.
- [ ] `T_network` real, share reported.
- [ ] LLM split present; overlay not credited with inference.
- [ ] Run counts: det ≥ 30, LLM ≥ 15; n/median/mean/stdev reported.
- [ ] Clock skew thresholded; excluded counted; `T_service` offset-corrected.
- [ ] Deployed-commit attestation per node (not just clean tree).
- [ ] Name-based routing proven by prefix-ownership in the answering node's marker.
- [ ] Deterministic multi-edge cells: ≥ 2 edges served; **LLM cells exempt and captioned as single/shared-edge** (NF-5).
- [ ] Context PIT intake-local; cold-start exclusion disclosed.
- [ ] Gate enforced before figures.
- [ ] D and E never share a table/figure/ratio; AST namespace test.
- [ ] Wire-format limitation stated prominently.

**Cross-cutting:**
- [ ] D does not claim to beat Temporal/Step Functions.
- [ ] E does not claim superiority over D.
- [ ] No "we beat X" language.
- [ ] All gates documented in `EXPERIMENT_PLAN.md` and `demo/README.md`.
- [ ] A-012 reverse clause absent; token presented as future work.

---

## 8. Risks & open questions v4

**Closed by v4 / PR-1:**
- ARCO v1 BLOCKER 1 (dead producer path) — closed by PR-1 §3.2a (corrected location, verified trace §0.1).
- ARCO v1 BLOCKER 2 (`send_response` no transmit) — closed by PR-1 §3.2b (Case A; forwarder-backed seam).
- ARCO v1 MAJOR 5 (late/duplicate crash) — closed by PR-1 §3.2e (+ Nack policy §3.2d).
- ARCO v1 MAJOR 6 (TaskGroup hang) — closed by PR-1 §3.2f.
- ARCO v1 MAJOR 7 (vacuous View B) — closed by real `T_service` + gate §4.5 cond. 7.
- ARCO v1 MAJOR 8 (gameable gates) — closed by §4.5/§5.4 (control binding, prefix-ownership, deployed-commit, AST namespace test).
- ARCO v1 MAJOR 9 (cost) — closed by §5.3 cost matrix + preflight.
- ARCO v1 MINOR 10/11/12 — closed by `emission_ts`, RunMetadata, four-PR spine.
- **ARCO v3 NF-1** (`send_response` Case B unroutable) — **closed: Case B deleted; Case A + forwarder-backed seam specified (§3.2b).**
- **ARCO v3 NF-2** (`data_from_lower` shape) — **closed: exact `[face_id, Interest]` contract + typed-inner variant + reply-face preservation + `test_inbound_request_translation` (§3.2a, §0.1).**
- **ARCO v3 NF-3** (response path unproven) — **closed structurally as a mandatory spike-first gate (§3.0, AC-PR1 #1). Runtime proof is the spike's job.**
- **ARCO v3 NF-4** (`reply_ref` union invariant) — **closed: Option 2 (side table) chosen and justified (§3.2a); union stays frozen.**
- **ARCO v3 NF-5** (`physical_publishable` cond. 7/8 scoping) — **closed: condition 8 required for deterministic multi-edge cells only; LLM cells exempt and captioned (§5.4).**
- **ADR A-012 revisions** — verified present and consistent: forward clause anchored at `port.py:265`; `NdnTlvEncoder` used (not SimpleStringEncoder/BasicTlvEncoder); two future-token tests are `skip` not `xfail` (A-012:133-143).

**Retained risks (live):**
1. **PR-1 spike outcome (HIGH until green).** Whether the stack delivers `[face_id, Interest]` and whether the response returns on the PIT face. Both read-verified (§0.1), neither runtime-proven. Mitigation: mandatory spike-first (§3.0); failing it stops the work and triggers explicit reply-face plumbing.
2. **Requester client-stack acceptance of producer-originated Content (MEDIUM).** The client stack (`port.py:91-98`) is exercised for fetch; its acceptance of the forwarder-returned Content is part of the spike. If it fails, the return path needs a small client-side sink rather than `queue_to_higher`.
3. **Bounded queues** `DEFAULT_INBOUND_SIZE=64` (`layer.py:47`), `MAX_CONTEXT_PIT_ENTRIES=256` (`context_pit.py:28`), `DEFAULT_QUEUE_SIZE=128` (`AsyncLayerStack.py:23`) PROVISIONAL; `port.py:319` awaits a full queue → possible serialization. D reports queue-bound effects; E confirms on hardware.
4. **`LatencyBackend` placement** pinned to the producer backend; test asserts invocation inside `hub.invoke`, not `send_request`.
5. **Clock domain mismatch for E** (`port.py:75` monotonic); offset correction + error bars; threshold open.
6. **Goldens:** `dispatch="serial"` default; golden re-run of the cardiac bus. Note: the cardiac bus still uses CS preload and is **not** migrated to registered producers — it stays the A/B/C structural runner. D/E use a new topology.
7. **LLM `n=15`** is a judgment call; the gate discloses it. If a venue demands 30, the preflight surfaces the cost and Dennis decides.
8. **Encoder additivity** is moot for the shipped work (no wire change), remains the future token milestone's largest unknown.

**Open questions for Dennis (decisidable) vs resolved:**
- **Resolved in v4:** NF-4 (Option 2 chosen, §3.2a), NF-5 (deterministic-only condition 8, §5.4), Nack policy default (name-LPM, §3.2d).
- **Decidable by Dennis (defaults chosen, no blocker):** (1) LLM `n=15` vs `30` (default 15 + bootstrap CI); (2) clock-skew threshold default 1 ms; (3) `--max-hours` preflight default 6 h. None blocks PR-1/PR-2/PR-3.

---

## 9. ARCO review records (self-contained)

### 9.1 ARCO v1 review outcome (2026-09-12) — VERDICT: BLOCK

ARCO adversarially reviewed v2 against the code. Verdict **BLOCK** — the design measured a topology the code does not have.

- **BLOCKER 1 — producer path is dead code.** `_on_inbound_request` (`layer.py:179-211`) is the only caller of `hub.invoke`, reached only via `InboundRequest`, produced only by adapter `inject_interest` — which has no production caller. `demo/cardiac_bus_topology.py:150-169` answers from the CS via `MgmtClient.add_new_content` with no `register_capability`. So `LatencyBackend` was never invoked and D's gate was unreachable.
- **BLOCKER 2 — `send_response` never transmits.** `port.py:212-213` only writes `_response_payloads`; no Content, no wire.
- **MAJOR 3 — A-012 token has no carrier** (Interest param encode/decode, InboundRequest plumbing, Content echo).
- **MAJOR 4 — A-012 verification grep wrong** (must assert absence of the reverse clause, not presence of the directional one).
- **MAJOR 5 — late/duplicate response crashes** (`context_pit.py:202-204`); Nack demux by dict order.
- **MAJOR 6 — TaskGroup failure semantics under-specified** → hang.
- **MAJOR 7 — D's `T_network` AND `T_service` near-zero** → View B vacuous.
- **MAJOR 8 — gates gameable** (control not cross-validated; "one non-intake leaf" weak; clean tree ≠ deployed commit; namespace test unspecified).
- **MAJOR 9 — ≥30 runs/cell × LLM = days** with no cost estimate; zero-latency redundant in LLM cells.
- **MINOR 10 — `T_decompose` mis-sourced.**
- **MINOR 11 — `config_snapshot` as `EventKind` abuses the schema.**
- **MINOR 12 — four-concern bundle.**

Preserved as GOOD: I2 analysis; index-keyed Merkle order-independence; the two fan-out paths distinction; two-view accounting; reverse-prefix diagnosis; honesty apparatus.

### 9.2 ARCO v3 re-review outcome (2026-09-12) — VERDICT: CONCERN

ARCO re-reviewed v3. **All 12 original findings RESOLVED.** Five NEW findings remained.

- **NF-1 (MAJOR) — `send_response` "Case B" is unroutable as written.** `PicnSubstratePort._lstack` is `[chunk, timeoutprevention, packetencoding, link]` (`port.py:91-98`) — no ICN layer; Content pushed onto `queue_from_higher` serialises onto the wire with no PIT face.
- **NF-2 (MAJOR) — `AgenticLayer.data_from_lower` contract under-specified.** The real item shape is `[face_id, Interest]` (ICN `:120-124` → TP `:138-139` → Chunk `:96` → NFN `:117-123` → `queue_from_lower`), face id at `[0]`.
- **NF-3 (MODERATE) — PR-1 response-path assumption must be proven by a mandatory live spike FIRST** — `handle_content` seeing a PIT entry with `local_app=False` (`ICNLayerCore.py:150-154`), reachable only if the injected Content traverses `handle_from_higher` (`:41`) and the PIT entry survives.
- **NF-4 (LOW) — `InboundRequest.reply_ref` (Option 1) breaks the closed-union invariant** (`events.py:99`).
- **NF-5 (LOW) — `physical_publishable` condition 7 can pass with a single non-intake leaf; condition 8 requires ≥2 edges only for deterministic cells.**

**NF-1..NF-5 disposition (all resolved in this v4):**

| Finding | Disposition in v4 | Where |
|---|---|---|
| NF-1 | **Resolved.** Case B deleted as a transmission path. Production = Case A only. Forwarder-backed response seam specified exactly (queue push to `queue_to_lower`); unit-test seam = injectable `response_sink` on `PicnSubstratePort`. | §3.2b |
| NF-2 | **Resolved.** Exact accepted input shapes pinned: canonical `[face_id, Interest]` + typed-`Outbound`/2-tuple inner-item variant; `data_from_lower` re-enqueues onto `self._inbound` so `_dispatch_event` routes to `_on_inbound_request`; must NOT assume bare `Interest`; reply face preserved. `test_inbound_request_translation` asserts preservation. | §3.2a, §0.1, AC-PR1 #3 |
| NF-3 | **Resolved (structurally).** PR-1's FIRST deliverable is a mandatory live spike with two assertions; both must pass before any D/E file is touched. Spike-first gate. | §3.0, AC-PR1 #1 |
| NF-4 | **Resolved.** Option 2 chosen (side table keyed by correlation in `AgenticLayer`), justified: keeps the closed union frozen, avoids editing both adapters' constructors, localises state to the owning layer. | §3.2a |
| NF-5 | **Resolved.** Condition 8 required for **deterministic** multi-edge cells only; LLM cells exempt, record `multi_edge_breadth_required=false`, and the report captions the LLM cell as single/shared-edge. The paper's multi-edge claim is scoped to deterministic cells. | §5.4 |

**ADR A-012 revisions — verified present and consistent with v4:**
- Two future-token tests are `skip`, not `xfail` (A-012:133-143, "A-012 future token: wire carrier not delivered"). ✅
- Forward clause anchored at `port.py:265` (A-012:24). ✅
- `NdnTlvEncoder` (not SimpleStringEncoder/BasicTlvEncoder) in the follow-on (A-012:34, :96, :148). ✅

---

*End of DESIGN v4 — FINAL, build-ready. No implementation code written — every Go/Python implementation item is CRAFT's. Every claim about existing code cites a verified file:line or is flagged in §0/§8. The v4 changes over v3 are: NF-1 Case B deletion + forwarder response seam; NF-2 exact `[face_id, Interest]` contract; NF-3 spike-first gate; NF-4 Option 2 (side table); NF-5 deterministic-only condition 8; plus the §9 self-contained ARCO v1+v3 records. The single unproven runtime assumption (the NF-3 response path) is deliberately front-loaded as PR-1's mandatory first deliverable — it must pass before D/E work begins.*