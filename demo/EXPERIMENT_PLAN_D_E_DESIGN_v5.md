# DESIGN v5 — Experiments D & E: Concurrent Fan-Out, T_intent Decomposition, and the Physical Testbed for Agentic Routing

**Author:** DESIREE 🏗️ (architecture & design)
**Status:** DESIGN v5 — **FINAL, build-ready** (supersedes v4; not implemented)
**Repo:** `gh-repositories/PiCN` (fork of Basel PiCN)
**Target:** revise COMMAG-26-00578 (rejected) + TNSM/Computer Networks reframing
**Date:** 2026-09-12
**Supersedes:** `EXPERIMENT_PLAN_D_E_DESIGN.md` (v1), `_v2.md` (v2), `_v3.md` (v3), `_v4.md` (v4).
**Incorporates:** all v4 content **unchanged except** the three code-contradicted findings from ARCO's v4 re-review (2 BLOCK-level, 1 MINOR) plus two ARCO checklist items. This file is a **targeted delta**; the unaffected v4 sections are inherited verbatim.

---

## v5 headline

ARCO's v4 re-review returned **BLOCK** on **two** new code-contradicted claims inside PR-1, plus **one** MINOR citation cleanup, plus two checklist items. All three findings are **confirmed true** against the code this revision (see §0.3). v5 is a **surgical revision** that:

1. **NF-6 (BLOCKER) — fixes the producer response path dead-end at NFN `handle_from_higher`.** Adds a downward pass-through to `NFNLayerCore.handle_from_higher` (option 1a, justified) so a `Content` pushed from `AgenticLayer` traverses NFN instead of being silently dropped. This is now a **`PiCN/` shared-core change**, owned by its own commit inside PR-1, with explicit sync/async parity.
2. **NF-7 (MAJOR) — starts the inbound pump on the port-less forwarder.** `AgenticLayer` drains `_inbound` without depending on `start_port()`/`_event_loop`. Adds spike assertion **(c)** so the spike can no longer pass while the producer path stays dead.
3. **NF-8 (MINOR) — removes the fabricated `Interceptor` symbol** from §0.1 and §9.1.
4. Records the two ARCO checklist items (bounded-queue pressure; PR-2's specific reverse-clause absence grep) in §8.
5. Updates A-012's cross-reference from the stale "design v3" to **v5**.

**BLOCK status after v5:** NF-6 and NF-7 are resolved structurally and made the acceptance subject of spike assertions (b) and (c). NF-6's runtime proof is now spike assertion (b) **on the real forwarder stack including NFN**, not on a bypassed NFN.

**Explicitly unchanged from v4 (ARCO cleared; do NOT touch):**
- NF-1..NF-5 structure and dispositions (§3.2a–f, §3.0, §5.4) — **except** §3.2a's `self._inbound` enqueue clause and §3.2b's response path, which are amended by NF-6/NF-7 below.
- The four-PR plan, scope, and dependency order (§2) — **except** PR-1's file list and blast radius, which NF-6 widens to include `PiCN/`.
- Gate scoping (§4.5, §5.4), the metric schema (§4.3, §6), the sweep (§4.4), the honesty apparatus (§7), and A-012's decision content (only its cross-reference line changes).
- A-012.

---

## 0. Verification notes (what I read this revision)

### 0.1 Re-verified against code for v5 (file:line)

**Every v4 §0.1 claim was re-read and remains true except the two corrected below.** The v4 producer-path trace steps 1–7 are confirmed unchanged. The following are the v5 re-reads:

**Producer (edge) side — the response return path (CORRECTED — NF-6):**
- `NFNLayerCore.handle_from_higher` (`NFNLayerCore.py:82-83`) is `def handle_from_higher(self, data) -> NFNCoreResult: return NFNCoreResult()` — **it unconditionally discards `data` and returns an empty result.** v4 §0.1 lines 40-44 and §3.2b lines 241-243 claimed the response path traverses `NFN handle_from_higher → chunk → timeoutprevention → ICN data_from_higher`; that is **code-contradicted and false**. The `Content` pushed onto `queue_to_lower` (= NFN's `queue_from_higher`) reaches `AsyncBasicNFNLayer.data_from_higher` (`AsyncBasicNFNLayer.py:151-155`), which calls the core and puts **nothing**; the packet is dropped. This was verified directly.
- The sync wrapper is identical: `BasicNFNLayer.data_from_higher` (`BasicNFNLayer.py:130-134`) calls `self._core.handle_from_higher(data)` and applies the (empty) result.
- The correct downstream machinery exists and is reachable **once NFN stops dropping the packet**: `AsyncBasicICNLayer.data_from_higher` (`AsyncBasicICNLayer.py:81-83`) → `ICNLayerCore.handle_from_higher` (`ICNLayerCore.py:35-44`) → for `Content` calls `handle_content(high_level_id, packet, from_local=True, has_to_higher=True)` (`ICNLayerCore.py:41`). `handle_from_higher` requires `data[0]` (int face id) and `data[1]` (packet); the `[face_id, content]` shape is correct (`ICNLayerCore.py:36-37`).
- `handle_content` (`ICNLayerCore.py:142-157`): with the inbound PIT entry present (face `local_app=False`, §0.1 step 4), the `has_to_higher and pit_entry.local_app[i]` branch is False, so it emits `Outbound("lower", [pit_entry.faceids[i], content])` (`ICNLayerCore.py:150-154`). v4's read of this branch is correct; what v4 got wrong was that the packet never reached it.

**Producer side — the interest upward pass-through (unchanged, the mirror for NF-6's fix):**
- `NFNLayerCore.handle_interest` (`NFNLayerCore.py:117-123`): for a non-NFN name, `direction = "queue_higher" if has_to_higher else "queue_lower"` and it re-emits `[packet_id, interest]` **unmodified**. `AsyncBasicNFNLayer.data_from_lower` passes `has_to_higher=to_higher is not None` (`AsyncBasicNFNLayer.py:157-163`). This is the existing upward fall-through that NF-6's downward fix mirrors.

**Producer side — the inbound event pump (CORRECTED — NF-7):**
- `AgenticLayer._event_loop` (`layer.py:159-165`) is the **only** drainer of `self._inbound`.
- `_event_loop` is created **only** in `start_port` (`layer.py:134-136`).
- `start_port` is called **only** when `self.agentic.port is not None` (`AgenticForwarder.py:183-184`). The D/E edge forwarder is constructed with **no** `port_adapter` (`AgenticForwarder.__init__` default `port_adapter=None`, `AgenticForwarder.py:72`, passed to `AgenticLayer(..., port=port_adapter)` at `AgenticForwarder.py:139-141`).
- `AsyncLayerProcess.run` (`AsyncLayerProcess.py:108-123`) pumps `queue_from_lower` → `data_from_lower` and `queue_from_higher` → `data_from_higher`; it does **not** know about `self._inbound`. So v4 §3.2a line 224's "enqueue onto `self._inbound`" has **no consumer** on the port-less forwarder. Confirmed: `T_service` would stay zero, exactly v1's dead-code defect resurfacing.
- `AsyncLayerStack.__init__` (`AsyncLayerStack.py:62-81`) wires `upper.queue_to_lower = q_to_lower`, `upper.queue_from_lower = q_to_upper`, `lower.queue_to_higher = q_to_upper`; NFC's `queue_to_higher` **is** Agentic's `queue_from_lower`. So `AgenticLayer.data_from_lower` **is** invoked by the stack's own run loop with no port. That is the hook NF-7 uses.
- `AgenticLayer.data_from_lower` (`layer.py:315-328`) is a confirmed no-op that discards `(to_lower, to_higher, data)`.

**FINDING 3 — the fabricated `Interceptor` symbol (CORRECTED — NF-8):**
- A recursive grep for `Interceptor` across `agentic/` returns **zero matches**. `layer.py:198-200` is `response = await self._hub.invoke(event.name, payload, deadline=deadline, quote_id=quote_id)`. The v4 §0.1 line 47 and §9.1 mentions of `Interceptor` are **fabricated**; the correct substance is that `layer.py:198` is the **only** call site of `hub.invoke` on the producer path (and `hub.invoke` → `producer.py` registry → backend is the only backend-invocation path).

**A-012 cross-reference:**
- `A-012-name-correlation-and-wildcards.md:7` reads "Experiment D/E design v3 (§2 PR-2, §4, §7)". Stale; updated to v5. (Grep for other "v3" references in A-012 text found none beyond this line; the body's citations are to code, not to a design version.)

### 0.2 Could NOT verify (flagged, no guessing)

1. **That the NF-6 downward pass-through returns Content to the requester end-to-end at runtime** (read says yes; spike assertion (b) is the proof, now on the real NFN-bearing stack).
2. **That the port-less inbound pump (NF-7) fires under the real forwarder's `AsyncLayerStack` run loop** (read says `data_from_lower` is called; spike assertion (c) is the proof).
3. **That the requester's client stack accepts the producer-originated Content** (`port.py:91-98`; unchanged from v4 §0.2 item 2; spike).
4. No `LatencyBackend`, `LatencyObserver`, `deploy/`, inventory, or Pi artifacts exist. Greenfield (unchanged).
5. A-012 encoder additivity (moot for shipped work; unchanged).

### 0.3 ARCO v4 findings — confirmed or refuted (with evidence)

| Finding | ARCO claim | DESIREE v5 verification | Verdict |
|---|---|---|---|
| **NF-6 (BLOCKER)** | v4 §0.1/§3.2b response path dead-ends at `NFN handle_from_higher` | `NFNLayerCore.py:82-83` returns empty unconditionally; `AsyncBasicNFNLayer.py:151-155` / `BasicNFNLayer.py:130-134` put nothing. **Confirmed.** | **TRUE — fixed** |
| **NF-7 (MAJOR)** | v4 §3.2a enqueues `InboundRequest` onto `_inbound` which is never drained on a port-less forwarder | `layer.py:159-165` only drainer; started only at `layer.py:134-136`; called only at `AgenticForwarder.py:183-184` when port≠None; D/E edge has `port_adapter=None`. **Confirmed.** | **TRUE — fixed** |
| **NF-8 (MINOR)** | `Interceptor` fabricated at `layer.py:198-200` | grep `Interceptor` in `agentic/` → **0 matches**; `layer.py:198-200` is `hub.invoke`. **Confirmed fabricated.** | **TRUE — removed** |

---

## 1. (unchanged from v4)

Goal & reviewer mapping, §1.1–§1.3: **inherited verbatim from v4.** No change.

---

## 2. Milestone / PR plan and dependency order (AMENDED — PR-1 only)

The four-PR plan, dependency order, PR-2/PR-3/PR-4 scope, and the PR ordering rule are **unchanged from v4 §2**. The **only** change is PR-1's scope/files/blast-radius, because NF-6 makes a `PiCN/` shared-core change part of PR-1.

### PR-1 — G.3 producer wiring (spike-gated)

**Scope (v5):** (0) **Mandatory live spike** with **three** assertions (a, b, c) proving the producer path and response return path **on the real NFN-bearing forwarder stack** (re-run/adapt of v4 assertion (b); new assertion (c)). (1) Make a capability Interest forwarded to a node with a registered capability reach `AgenticLayer._on_inbound_request` → `hub.invoke`. (2) Transmit the response as a `Content` the requester's port demuxes to the originating correlation, via a **forwarder-backed response seam** on `AgenticLayer` (Case A only) **plus a downward pass-through in `NFNLayerCore.handle_from_higher`** so the Content actually reaches ICN (NF-6). (3) Start an inbound pump on the port-less forwarder so `_inbound` is drained (NF-7). (4) Hardening: idempotent late/duplicate response, Nack correlation policy, deterministic TaskGroup failure semantics. Contains **no wire-format change**.

**Files (v5 — `PiCN/` widened by NF-6):**
- `PiCN/ProgramLibs/AgenticForwarder/AgenticForwarder.py` *(existing)*
- **`PiCN/Layers/NFNLayer/NFNLayerCore.py`** *(NEW in v5 — the NF-6 downward pass-through; shared core)*
- `agentic/agentic_layer/layer.py` *(existing)*
- `agentic/adapters/picn/port.py` *(existing)*
- `agentic/port/events.py` *(comment-only edit if any)*
- tests under `agentic/tests/` **and** `PiCN/Layers/NFNLayer/test/` (sync/async parity tests for NF-6)

**Blast-radius statement (NEW in v5, explicit):** PR-1 is **no longer an agentic-only change**. It now edits a `PiCN/` shared core (`NFNLayerCore`). Per `AGENTS.md`:
- **Protocol logic lives in `*LayerCore.py`.** The NF-6 fix is a core change, not a wrapper change; the sync `BasicNFNLayer` and async `AsyncBasicNFNLayer` must not diverge. Both call `handle_from_higher`; the pass-through is therefore runtime-agnostic by construction.
- **"Do not change network behaviour while changing architecture."** The NF-6 pass-through only changes what `handle_from_higher` does with a `Content` it **currently discards**: it forwards it downward exactly as `handle_from_lower` already forwards a non-NFN Interest upward. No packet format, FIB/PIT/CS logic, or forwarding semantic changes. It is a *reachability* fix for a packet the layer already drops.
- **One concern per commit.** NF-6 belongs to PR-1 (it is required for PR-1's response path) but **must be its own commit within PR-1**, clearly flagged (see §3.2b "Commit plan").

**AC-PR1 (v5 — each is a runnable command or named test):**
1. **SPIKE GATE (mandatory, runs first):** `python -m pytest agentic/tests/test_producer_path_spike.py -v` — **three** assertions, all must pass:
   - **(a)** `queue_higher` reaches `AgenticLayer.data_from_lower` with `data == [face_id, Interest]` and `data[0]` is the requester's face id (§3.1 assertion (a)).
   - **(b)** an injected `Content` sent down the **real forwarder stack including NFN** reaches `handle_content` and is emitted `Outbound("lower", [requester_face, content])` — **this assertion FAILS on pre-NF-6 code** (the packet is dropped at `NFNLayerCore.handle_from_higher`); it is the acceptance test for the NF-6 fix (§3.1 assertion (b)).
   - **(c)** an Interest driven through the forwarder stack results in `_on_inbound_request` being invoked **and** `hub.invoke` being called (spy) on a port-less forwarder — **this assertion FAILS on pre-NF-7 code** (`_inbound` is never drained, so `_dispatch_event` never runs); it is the acceptance test for the NF-7 fix (§3.1 assertion (c)).
2. `python -m pytest agentic/tests/test_producer_path_live_roundtrip.py -v` — SimulationBus: ambulance `submit_intent` → edge `AgenticForwarder` with a **registered** `DeterministicBackend` → `hub.invoke` **called** (spy) → `Content` returns → ambulance Context PIT trace root verified; `submit_intent` returns within timeout.
3. `python -m pytest agentic/tests/test_inbound_request_translation.py -v` — `data_from_lower` receives `[face_id, Interest]`, emits `InboundRequest` with **reply ref preserved and equal to `data[0]`** (now via the NF-7 pump).
4. `python -m pytest agentic/tests/test_send_response_content.py -v` — `send_response` (forwarder-backed) yields a `Content` that traverses NFN and is demuxed by the requester port to the correct correlation.
5. **`python -m pytest PiCN/Layers/NFNLayer/test/test_nfn_handle_from_higher_passthrough.py -v`** *(NEW in v5)* — NF-6 parity test for the shared core: a non-NFN `Content` in `[face_id, Content]` is re-emitted `Outbound("lower" or "queue_lower", [face_id, Content])` by **both** `BasicNFNLayer` and `AsyncBasicNFNLayer`; an NFN-marker packet keeps its existing handling; a malformed input returns empty without raising.
6. `python -m pytest agentic/tests/test_late_response_idempotent.py -v` — duplicate/late `ResponseArrived` after termination dropped, no `ValueError`, root unchanged.
7. `python -m pytest agentic/tests/test_nack_correlation.py -v` — Nack correlates by name-LPM; no match → drop, not mis-attribute.
8. `python -m pytest agentic/tests/test_taskgroup_failure_semantics.py -v` — one leaf fails/cancels → `NULL_RESPONSE` at that leaf, `done` resolves, root deterministic.
9. `python -m pytest agentic/tests/test_submit_intent_serial_golden.py -v` — serial path unchanged (root + dispatch count identical to pre-PR-1).
10. `python -m pytest agentic/tests/test_picn_adapter.py agentic/tests/test_port.py agentic/tests/test_mock_adapter.py agentic/tests/test_layer_plugin_mock.py agentic/tests/test_nfn_passthrough.py -v` — no regression (note: the existing `test_nfn_passthrough.py` covers the **upward** pass-through; NF-6 must not perturb it).

**Does NOT absorb:** the A-012 reverse-clause deletion (PR-2), any `LatencyObserver`/`LatencyBackend` (PR-3), any experiment runner/topology (PR-3/PR-4), any wire-format change (future work).

**Unblocks:** PR-3 (`T_service` measurable once the NF-7 pump makes `hub.invoke` reachable and the NF-6 pass-through makes the reply return), PR-4 (UDP return path exists).

### PR-2 / PR-3 / PR-4 (unchanged from v4)

**Inherited verbatim from v4 §2.** No change. (PR-2 gains one checklist note in §8; its scope is unchanged.)

### PR ordering rule (unchanged)

Unchanged from v4 §2 *except* the added clause: **within PR-1, the NF-6 `NFNLayerCore` commit lands before the response-seam commit**, because the seam's spike assertion (b) cannot pass without it. This is an intra-PR ordering constraint, not a new PR.

---

## 3. PR-1 — G.3 producer wiring design

### 3.0 The spike must precede everything (NF-3, amended by NF-6/NF-7)

The response path still rests on read-verified-but-runtime-unproven facts, but v5 **corrects which facts** they are. HF-3 makes proving them the first deliverable of PR-1. Order of work:

```
SPIKE (assertions a, b, c)  ──PASS──►  producer translation ──►  NF-6 pass-through ──►  NF-7 pump ──►  response seam ──►  hardening
        │
        └──FAIL──►  STOP. Do not touch any D/E file. Record which assertion failed:
                    (a) stack does not preserve the reply face;
                    (b) NFN does not pass the Content down (NF-6 not applied or wrong shape);
                    (c) the port-less pump does not fire (NF-7 not applied).
                    Each has a named fallback in §3.2b/§3.2a.
```

The spike is a standalone test file (`agentic/tests/test_producer_path_spike.py`) using a real `AgenticForwarder` on a `SimulationBus`, **including the NFN layer** (do not bypass NFN — bypassing it is what hid NF-6). It touches only the forwarder stack; no D/E file is created until it is green. §0.2 items 1/2 are the top open risks until then.

### 3.1 The topology D will use **after** PR-1 (unchanged; response path annotation corrected)

The topology diagram is **unchanged from v4 §3.1**; the only annotation change is that the arrow `send_response → Content down forwarder` now explicitly means `Content down forwarder **through NFN (NF-6 pass-through)** → chunk → timeoutprevention → ICN data_from_higher`.

**Spike assertions (v5 — three):**
- **(a)** After a capability Interest is put on the edge stack's link-facing side, `AgenticLayer.data_from_lower` is called with `[face_id, Interest]` and `data[0]` equals the requester's face id.
- **(b)** After `_on_inbound_request` builds a `Content`, pushing it onto `AgenticLayer.queue_to_lower` results in: `AsyncBasicNFNLayer.data_from_higher` re-emitting it downward (NF-6), then `ICNLayerCore.handle_content` emitting `Outbound("lower", [requester_face, content])`. **Assertion (b) must be exercised on the real stack with NFN present.**
- **(c)** On a forwarder constructed with `port_adapter=None`, an Interest reaching the agentic layer causes `_on_inbound_request` to be invoked **and** `hub.invoke` to be called (spy), proving the NF-7 inbound pump runs without `start_port()`.

### 3.2 Exact code to build (description only — CRAFT implements)

#### 3.2a Interest → `InboundRequest` translation (AMENDED — NF-7)

**Location:** `AgenticLayer.data_from_lower` (`layer.py:315-328`), replacing the no-op. **(unchanged from v4)**

**NF-2 input-shape contract:** **unchanged from v4 §3.2a** — accepts the canonical `[face_id, Interest]` 2-element list and a 2-tuple inner-item variant; malformed input returns without raising; `Content` ignored at this layer; name conversion via `PiCN.Packets` (AC2); `layer.py` must not import the adapter.

**NF-7 — how the port-less forwarder drains `self._inbound` (AMENDED).** The v4 clause "enqueue onto `self._inbound` so `_dispatch_event` routes it" is **incomplete**: on a port-less forwarder `_inbound` has no consumer (§0.1). v5 specifies **one** mechanism, replacing that clause:

- **`AgenticLayer.data_from_lower` dispatches directly when no port pump is running.** Concretely: `data_from_lower` translates the packet to an `InboundRequest` **and** invokes `_dispatch_event(event)` inline (i.e. `await self._dispatch_event(inbound)`), because on the stack-driven path the layer's own run loop (`AsyncLayerProcess.run` → `_pump_lower` → `data_from_lower`) **is** the pump. There is no queue to enqueue onto that lacks a consumer.
- **When a port IS attached** (`self._started_port` True), `data_from_lower` continues to enqueue onto `self._inbound` so the port's events and stack events share one ordered pump (the event loop may already be running; direct dispatch could interleave). The selection is a single condition: **`if self._started_port: await self._inbound.put(inbound) else: await self._dispatch_event(inbound)`**.
- **Reconciling with v4's prohibition (line ~224).** v4 forbade calling `_on_inbound_request` **directly** to avoid "bypass[ing] the single event pump and reorder[ing] under concurrency." The concern (single ordered pump) is preserved: `data_from_lower` calls **`_dispatch_event`**, the same dispatcher the pump uses, not `_on_inbound_request`. On the port-less path the layer's run loop is itself single-threaded per layer and awaits each `data_from_lower` serially (`AsyncLayerProcess.py:108-111`), so ordering is maintained. There is **exactly one** dispatcher (`_dispatch_event`) and **at most one** pump per node configuration.
- **Migration note for CRAFT:** this is a deliberate, documented deviation from v4's literal text. Do **not** enqueue unconditionally onto `_inbound`; that is the defect. Do **not** call `_on_inbound_request` directly; that is the other defect v4 correctly prohibited.

**NF-4 — reply-face carriage: Option 2 (side table).** **Unchanged from v4 §3.2a.**

#### 3.2b `send_response` builds a `Content` and transmits it (AMENDED — NF-6, the BLOCKER fix)

**NF-1 disposition (unchanged):** v3 "Case B" remains deleted; production is **Case A only** (forwarder/ICN-backed).

**NF-6 — the v4 response path is corrected: NFN must pass the `Content` down.** v4 §0.1 lines 40-44 and §3.2b lines 241-243 asserted `AgenticLayer → queue_to_lower → NFN handle_from_higher → chunk → timeoutprevention → ICN data_from_higher → handle_content`. The segment `NFN handle_from_higher → …` **does not exist**: `NFNLayerCore.handle_from_higher` (`NFNLayerCore.py:82-83`) returns an empty `NFNCoreResult()` and the packet is dropped (`AsyncBasicNFNLayer.py:151-155`). **The response path as designed is dead at NFN.**

**DESIGN REQUIREMENT — choose one, justify, specify exactly.**

**CHOSEN: Option (1a) — add a downward pass-through to `NFNLayerCore.handle_from_higher`.**

**Justification (1a over 1b).**
- **Symmetry with the existing upward pass-through.** `handle_interest` already implements exactly this pattern for the upward direction: a non-NFN packet is re-emitted `"queue_higher" if has_to_higher else "queue_lower"` with the **same `packet_id`** (`NFNLayerCore.py:117-123`). The downward fix is the mirror image and uses the same discriminator. Option (1b) (route the response around NFN, injecting at the ICN's higher interface) would require the agentic layer to reach past NFN into a lower layer's queue — breaking the stack abstraction, duplicating the layer-wiring knowledge in `AgenticLayer`, and diverging from how **every other packet** traverses the stack. It also creates a second, non-uniform path that D/E would then measure as if it were the real stack.
- **Minimal blast radius, no wire change, guardable by test.** One method, mirroring an existing one. It changes behaviour only for a packet NFN **currently discards**.
- **Option (1b) retained as fallback only if assertion (b) shows the pass-through cannot be made to work** (e.g. if chunk/timeoutprevention mangle a downward `Content`); that fallback is specified at the end of this subsection so CRAFT has a stop condition.

**Exact method to change:** `NFNLayerCore.handle_from_higher` (`NFNLayerCore.py:82-83`). **No wrapper change** — `BasicNFNLayer.data_from_higher` (`BasicNFNLayer.py:130-134`) and `AsyncBasicNFNLayer.data_from_higher` (`AsyncBasicNFNLayer.py:151-155`) already call the core and apply its outbounds; putting the logic in the core is what keeps the two runtimes from diverging (`AGENTS.md` "Protocol logic lives in `*LayerCore.py`").

**Exact behavior — how the core distinguishes an agentic capability-response `Content` from an NFN computation packet.** The discriminator is **packet type plus name marker, in this order**:
1. Unpack the inbound item exactly as `handle_from_lower` does: if `data` is a list/tuple of length 2, `packet_id = data[0]`, `packet = data[1]`; else treat `data` as the packet with a default id (mirror `NFNLayerCore.py:86-91`). `ICNLayerCore.handle_from_higher` uses the same `[face_id, packet]` contract (`ICNLayerCore.py:36-37`), so the shape is consistent stack-wide.
2. If `packet` is **not** an `Interest`/`Content`/`Nack`, return `NFNCoreResult()` (drop, as today).
3. If `packet` is an `Interest`: **route to the existing `handle_interest`** (unchanged; preserves any future Interest-from-higher use and the NFN marker check).
4. If `packet` is a `Content` whose name's last component is **not** `b"NFN"` and the name is **not** an R2C name (`self.r2cclient.R2C_identify_Name(packet.name)` is False): **re-emit downward** as `Outbound("queue_lower", [packet_id, packet])`. This is the agentic capability response. The name marker is the same discriminator `handle_interest` uses at line 117; NFN computation Content is always named for the NFN computation (`…/NFN` or an R2C name) and is handled by the existing NFN path, never passed through.
5. If `packet` is a `Content` that **is** NFN-marked/R2C, or a `Nack`: **preserve current behaviour** (no downward pass-through; the NFN computation table owns it). Note: today `handle_from_higher` drops all of these; the conservative v5 change is to pass through only the non-NFN/agentic `Content`, and leave NFN-marked input returning empty exactly as before. (If ARCO or CRAFT finds a legitimate NFN Content that must enter NFN from above, that is a separate concern and must not be bundled here.)
6. Direction token choice: use `"queue_lower"` (the layer's instance queue, `self.queue_to_lower`) to mirror the upward branch's `"queue_higher"` (`NFNLayerCore.py:121-122`) and because that is the queue NFN's own `data_from_higher` wrapper applies to. **Do not** use `"lower"` (the handler argument) for the pass-through; the symmetric upstream branch uses the instance-queue token. CRAFT must confirm the async `_apply_outbound` maps `"queue_lower"` → `self.queue_to_lower.put` (`AsyncBasicNFNLayer.py:114-116`) and sync `_apply_outbound` maps it identically (`BasicNFNLayer.py:99-101`).

**Sync/async parity requirement (explicit).** Because the logic lands in `NFNLayerCore.handle_from_higher`, both `BasicNFNLayer` and `AsyncBasicNFNLayer` inherit identical behaviour with **no wrapper edits**. The parity is enforced by AC-PR1 #5: `PiCN/Layers/NFNLayer/test/test_nfn_handle_from_higher_passthrough.py` must exercise **both** wrappers against the same core and assert identical outbounds. This is the `AGENTS.md` rule made a runnable test.

**Commit plan (explicit, because this is a shared-core change).** Within PR-1:
- **Commit 1 (NF-6):** `NFNLayerCore.handle_from_higher` pass-through **only** + `PiCN/Layers/NFNLayer/test/test_nfn_handle_from_higher_passthrough.py`. One concern: "NFN: pass non-NFN Content from higher downward". No agentic files. This commit is independently mergeable and reverts cleanly.
- **Commit 2 (NF-7):** `AgenticLayer.data_from_lower` port-less dispatch + spike assertion (c) + translation test.
- **Commit 3:** forwarder-backed response seam (v4 §3.2b) + `AgenticForwarder` wiring.
- **Commit 4:** hardening (NF-1 idempotency, Nack policy, TaskGroup semantics).
This satisfies "one concern per commit" while keeping all four in PR-1 because they are jointly required for PR-1's single goal.

**The forwarder-backed response seam (unchanged from v4 §3.2b).** `AgenticLayer._transmit_response(reply_ref, name, payload)` builds `Content(to_picn_name(name), payload)` and awaits `self.queue_to_lower.put([reply_ref, content])`; `_on_inbound_request` calls it instead of `self._port.send_response(...)` when forwarder-backed. The unit-test sink on `PicnSubstratePort` is unchanged. **Corrected annotation:** the Content placed on `queue_to_lower` traverses **NFN (via the NF-6 pass-through)**, then chunk → timeoutprevention → ICN `data_from_higher` → `handle_content`.

**Option (1b) fallback (only if assertion (b) fails after Commit 1).** If the downward `Content` cannot survive chunk/timeoutprevention, the fallback is a dedicated response seam that injects at the **ICN's** higher interface: `AgenticLayer` cannot reach ICN directly (stack abstraction), so the seam must be a forwarder-owned callable installed on `AgenticLayer` by `AgenticForwarder`, which puts `[reply_ref, content]` onto `self.icnlayer.queue_from_higher` and lets ICN's normal `data_from_higher` run. This is **second choice**: it bypasses NFN and must then be labelled in the design as a non-uniform response path, and D/E must not claim it is the default NFN stack. v5 expects Commit 1 to make 1b unnecessary; CRAFT stops and reports if it does not.

#### 3.2c Correlation plumbing (no wire token — Option 3)

**Unchanged from v4 §3.2c.**

#### 3.2d Correlation-carrying Nack OR documented limitation

**Unchanged from v4 §3.2d.**

#### 3.2e Idempotent late/duplicate response

**Unchanged from v4 §3.2e.**

#### 3.2f Deterministic TaskGroup failure semantics

**Unchanged from v4 §3.2f.**

### 3.3 Verification (PR-1) — v5

New tests under `agentic/tests/` and `PiCN/Layers/NFNLayer/test/`:

| Test | Asserts |
|---|---|
| `agentic/tests/test_producer_path_spike.py` | **(gate, first)** (a) `[face_id, Interest]` reaches `data_from_lower` with `data[0]` = requester face; (b) injected `Content` down the **real NFN-bearing** forwarder stack → `Outbound("lower", [requester_face, content])` (**fails pre-NF-6**); (c) port-less forwarder → `_on_inbound_request` invoked **and** `hub.invoke` called (spy) (**fails pre-NF-7**) |
| `agentic/tests/test_producer_path_live_roundtrip.py` | SimulationBus: ambulance submit_intent → edge registered producer → `hub.invoke` **called** (spy) → Content returns → trace root verified; returns within timeout |
| `agentic/tests/test_inbound_request_translation.py` | `data_from_lower` receives `[face_id, Interest]`, emits `InboundRequest` with reply ref preserved (`== data[0]`); malformed input returns without raising |
| `agentic/tests/test_send_response_content.py` | forwarder-backed `send_response` yields a `Content` on the wire (traversing NFN); requester port demuxes to the right correlation |
| **`PiCN/Layers/NFNLayer/test/test_nfn_handle_from_higher_passthrough.py`** | **(NEW — NF-6)** non-NFN `Content` re-emitted downward; NFN-marked/R2C Content and Nack preserve prior (empty) behaviour; malformed input returns empty; **sync and async wrappers identical** |
| `agentic/tests/test_late_response_idempotent.py` | duplicate/late `ResponseArrived` after termination dropped, no `ValueError`, root unchanged |
| `agentic/tests/test_nack_correlation.py` | Nack with a name correlates via LPM; no match → drop, not mis-attribute |
| `agentic/tests/test_taskgroup_failure_semantics.py` | one leaf fails/cancels → NULL at that leaf, `done` resolves, root deterministic |
| `agentic/tests/test_submit_intent_serial_golden.py` | serial path unchanged (root + dispatch count identical to pre-PR-1) |

Run (spike first, then parity, then the rest):
```bash
python -m pytest agentic/tests/test_producer_path_spike.py -v \
  && python -m pytest PiCN/Layers/NFNLayer/test/test_nfn_handle_from_higher_passthrough.py -v \
  && python -m pytest agentic/tests/test_producer_path_live_roundtrip.py \
       agentic/tests/test_inbound_request_translation.py \
       agentic/tests/test_send_response_content.py \
       agentic/tests/test_late_response_idempotent.py \
       agentic/tests/test_nack_correlation.py \
       agentic/tests/test_taskgroup_failure_semantics.py \
       agentic/tests/test_submit_intent_serial_golden.py -v
```

---

## 4. Experiment D (unchanged from v4)

§4.0–§4.7: **inherited verbatim from v4.** The producer path that D measures is now the NF-6/NF-7-corrected one; no D metric, sweep, or gate changes.

---

## 5. Experiment E (unchanged from v4)

§5.0–§5.8: **inherited verbatim from v4.**

---

## 6. Metric schema evolution v5 (unchanged from v4)

§6.1–§6.5: **inherited verbatim from v4.** No schema change in v5.

---

## 7. Honesty & anti-strawman checklist v5 (unchanged from v4)

**Inherited verbatim from v4**, with the wire-format limitation statement unchanged. **No change** — NF-6/NF-7/NF-8 alter implementation reachability, not any claim, gate, or caption.

---

## 8. Risks & open questions v5

### 8.1 Closed by v5 / PR-1

- ARCO v1 BLOCKER 1 — closed by PR-1 §3.2a.
- ARCO v1 BLOCKER 2 — closed by PR-1 §3.2b.
- ARCO v1 MAJOR 5/6/7/8/9, MINOR 10/11/12 — closed (unchanged from v4 §8).
- ARCO v3 NF-1..NF-5 — closed (unchanged from v4 §8).
- **ARCO v4 NF-6 (BLOCKER) — response path dead-ends at NFN `handle_from_higher`. CLOSED: Option (1a) chosen and specified: downward pass-through in `NFNLayerCore.handle_from_higher`, same discriminator as the existing upward pass-through, governed by `AGENTS.md` core/parity rules, its own commit inside PR-1, acceptance = spike assertion (b) + `test_nfn_handle_from_higher_passthrough.py` (sync/async).**
- **ARCO v4 NF-7 (MAJOR) — inbound pump never runs on the port-less forwarder. CLOSED: `AgenticLayer.data_from_lower` dispatches via `_dispatch_event` when `_started_port` is False, enqueues onto `_inbound` when True; single dispatcher preserved; acceptance = spike assertion (c) + `test_inbound_request_translation.py`.**
- **ARCO v4 NF-8 (MINOR) — fabricated `Interceptor`. CLOSED: removed from §0.1 and §9.1; grep `Interceptor` across `agentic/` returns 0 matches; the correct substance (hub.invoke is the only backend-invocation path at `layer.py:198`) is retained.**

### 8.2 ARCO checklist items recorded (NEW in v5)

1. **Bounded-queue pressure once the inbound path carries producer traffic (recorded, PROVISIONAL).** `DEFAULT_INBOUND_SIZE=64` (`layer.py:47`), `DEFAULT_QUEUE_SIZE=128` (`AsyncLayerStack.py:23`), `MAX_CONTEXT_PIT_ENTRIES=256` (`context_pit.py:28`). With NF-7, producer interests now flow through the same stack queues, and `_inbound` carries producer requests on port-attached nodes. PR-3 must measure queue-bound effects and report them; PR-4 confirms on hardware. **Action:** PR-1's spike assertion (c) and the live roundtrip should log peak queue depth; a full sweep is PR-3's. No bound is changed in PR-1.
2. **PR-2 must use the specific reverse-clause absence grep, not a bare `is_prefix_of`.** The correct AC-PR2 command is `! grep -n "content_name.is_prefix_of" agentic/adapters/picn/port.py` — a bare `is_prefix_of` grep **false-matches** the legitimate `prefix.is_prefix_of(name)` at `port.py:172` (`longest_prefix_match`). **This is already the v4 §2 AC-PR2 #1 text; v5 re-affirms it explicitly** so it is not "simplified" during implementation.

### 8.3 Retained risks (live) — v4 §8 list, amended

v4 §8 retained risks 1–8 carry over. Amended items:
- **Risk 1 (PR-1 spike outcome).** Now **three** assertions. (b) and (c) are **known-red** on pre-fix code (the fixes are NF-6/NF-7), so the spike is a genuine regression gate, not a formality. HIGH until green.
- **Risk 3 (bounded queues).** Elevated by NF-7 (the inbound path now carries producer traffic); see §8.2 item 1.
- **NEW risk (NF-6 blast radius).** The fix touches a shared `PiCN/` core used by **every** NFN node (sync and async). Mitigation: the change only affects a packet NFN currently discards; the existing `test_nfn_passthrough.py` plus the new parity test guard the upward path and both runtimes. Any NFN node that today relies on `handle_from_higher` dropping Content is relying on data loss; no legitimate caller does.

### 8.4 Open questions for Dennis (v5)

Unchanged from v4 §8, **except** one new decidable item:
- **(NEW) NF-6 fallback authorization.** If spike assertion (b) fails after Commit 1, CRAFT stops and DESIREE/Dennis decide whether to take Option (1b) (ICN-higher injection, labelled non-uniform) or to debug the chunk/timeoutprevention downward path. Default: prefer debugging 1a; take 1b only with explicit sign-off.

---

## 9. ARCO review records (self-contained)

### 9.1 ARCO v1 review outcome (2026-09-12) — VERDICT: BLOCK

**Inherited verbatim from v4 §9.1.** One correction: v4 §9.1's GOOD list included the fabricated `Interceptor` framing. **The correct statement is: producer-side backend invocation happens only at `layer.py:198-200` (`await self._hub.invoke(...)`), which is the only call site of `hub.invoke` on the producer path.** There is no `Interceptor` symbol in the codebase.

### 9.2 ARCO v3 re-review outcome (2026-09-12) — VERDICT: CONCERN

**Inherited verbatim from v4 §9.2**, including the NF-1..NF-5 disposition table. Unchanged.

### 9.3 ARCO v4 re-review outcome (2026-09-12) — VERDICT: BLOCK

ARCO re-reviewed v4 against the code. The v4 headline changes were endorsed, but **two new code-contradicted findings inside PR-1** (BLOCK-level) and one MINOR citation cleanup remained, plus two checklist items.

- **NF-6 (BLOCKER) — producer response path dead-ends at NFN `handle_from_higher`.** v4 §0.1 (~lines 40-44) and §3.2b (~lines 241-243) claimed `AgenticLayer.queue_to_lower → NFN handle_from_higher → chunk → timeoutprevention → ICN data_from_higher → handle_content`. `NFNLayerCore.handle_from_higher` (`NFNLayerCore.py:82-83`) unconditionally returns an empty `NFNCoreResult()`; the `Content` is silently dropped and never reaches ICN. Verified true in v5 §0.3.
- **NF-7 (MAJOR) — the inbound event pump never runs on the port-less forwarder.** v4 §3.2a (~line 224) enqueues `InboundRequest` onto `self._inbound`, but `_event_loop` (`layer.py:159-165`) is started only by `start_port()` (`layer.py:126-136`), which `AgenticForwarder` calls only when `self.agentic.port is not None` (`AgenticForwarder.py:183-184`). The D/E edge forwarder has no `port_adapter`, so `_on_inbound_request` is never reached and `hub.invoke` never runs. Verified true in v5 §0.3.
- **NF-8 (MINOR) — fabricated `Interceptor` symbol.** v4 §0.1 (~line 47) and §9.1 cite `Interceptor` at `layer.py:198-200`; no such symbol exists. Verified true in v5 §0.3.

**NF-6/NF-7/NF-8 disposition (all resolved in this v5):**

| Finding | Disposition in v5 | Where |
|---|---|---|
| NF-6 | **Resolved structurally.** Option (1a) chosen and justified: downward pass-through in `NFNLayerCore.handle_from_higher`, same packet-type + name-marker discriminator as the existing upward pass-through (`NFNLayerCore.py:117-123`); core-only change preserving sync/async parity; own commit inside PR-1; acceptance = spike assertion (b) + sync/async parity test. `PiCN/` blast radius stated. Option (1b) kept as sign-off-only fallback. | §3.2b, §2 PR-1, §3.3 |
| NF-7 | **Resolved structurally.** `AgenticLayer.data_from_lower` dispatches via `_dispatch_event` when no port pump runs, enqueues onto `_inbound` when it does; single dispatcher preserved; v4's "do not call `_on_inbound_request` directly" reconciled. Acceptance = spike assertion (c) + translation test. | §3.2a, §3.3 |
| NF-8 | **Resolved.** Fabricated `Interceptor` removed from §0.1 and §9.1; correct substance (only caller of `hub.invoke` is `layer.py:198`) retained. | §0.1, §9.1 |
| Checklist 1 | **Recorded.** Bounded-queue pressure under producer traffic noted for PR-3/PR-4. | §8.2 |
| Checklist 2 | **Re-affirmed.** PR-2 uses the specific absence grep, not bare `is_prefix_of`. | §8.2 |

**ADR A-012 — cross-reference updated.** `A-012-name-correlation-and-wildcards.md:7` changed from "Experiment D/E design v3 (§2 PR-2, §4, §7)" to "**Experiment D/E design v5 (§2 PR-2, §4, §7)**". No other A-012 content touched (decision, options, verification commands unchanged; ARCO cleared them).

---

*End of DESIGN v5 — FINAL, build-ready. No implementation code written — every implementation item is CRAFT's. Every claim about existing code cites a verified file:line or is flagged in §0.2/§8.3. **The rest of v4 (§1, §4, §5, §6, §7, and the PR-2/PR-3/PR-4 definitions in §2) is unchanged and is incorporated by reference.** v5's changes over v4 are exactly: NF-6 (NFN downward pass-through, `PiCN/` blast radius, Option 1a chosen), NF-7 (port-less inbound dispatch, assertion (c)), NF-8 (fabricated-symbol removal), the two §8.2 checklist items, and the A-012 v3→v5 cross-reference. The producer path now has three mandatory spike assertions (a, b, c); (b) and (c) are red on pre-fix code, which is the point.*