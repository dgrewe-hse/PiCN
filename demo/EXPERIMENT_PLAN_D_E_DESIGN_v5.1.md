# DESIGN v5.1 — Experiments D & E: Surgical micro-revision of v5 (NF-9 + NF-10)

**Author:** DESIREE 🏗️ (architecture & design)
**Status:** DESIGN v5.1 — **FINAL, build-ready** (supersedes v5 **only for the amended sections listed below**; v5 stands unchanged for the rest)
**Repo:** `gh-repositories/PiCN` (fork of Basel PiCN)
**Target:** revise COMMAG-26-00578 (rejected) + TNSM/Computer Networks reframing
**Date:** 2026-09-13
**Supersedes (scoped):** `EXPERIMENT_PLAN_D_E_DESIGN_v5.md` (v5) for **§0.1, §3.2a, §3.2b, §3.1 assertion (c), AC-PR1 #1(c), §8** only.
**Incorporates:** ARCO's v5 re-review (CONCERN): **one MAJOR code-contradicted hole (NF-9)** plus **one MINOR documentation gap (NF-10)**. ARCO cleared NF-6/NF-7/NF-8 as **correct**.

---

## v5.1 headline

ARCO verified v5's NF-6/NF-7/NF-8 fixes are **all correct**, and found **one new code-contradicted hole inside PR-1** plus one MINOR documentation gap. Both are confirmed true against the code this revision (see §0.4). v5.1 is a **surgical micro-revision** that:

1. **NF-9 (MAJOR) — the response terminus still hard-requires a port.** NF-7's fix makes a port-less interest reach `_on_inbound_request`, but that method still asserts a port at `layer.py:181` (**before** `hub.invoke` at `layer.py:198`) and unconditionally calls `self._port.send_response(...)` at `layer.py:208`. On the exact port-less edge forwarder D/E use (`AgenticForwarder(port=0, ...)`, no `port_adapter`), **spike assertion (c) throws at line 181 before `hub.invoke`** — it is unsatisfiable as specified, and `T_service` stays unreachable. v5.1 makes `_on_inbound_request` **port-independent**, routing the reply over the **`queue_to_lower` response seam** when there is no port.
2. **NF-10 (MINOR) — Nack-from-higher asymmetry documented as deliberately out of scope.** The NFN pass-through §3.2b step 5 preserves "drop" for a `Nack` from higher; a capability backend that Nacks would be silently dropped at NFN and reach `_on_response`/`_on_failed` only via `RequestTimedOut`. v5.1 states this is **deliberate, not forgotten** (§3.2b step 5 + §8).

**BLOCK status after v5.1:** NF-9 is resolved structurally; its acceptance is a **strengthened** spike assertion (c) that now proves the **response seam ran**, not merely that `hub.invoke` was called. NF-6/NF-7/NF-8 remain as v5 specified.

**Explicitly unchanged from v5 (do NOT touch):**
- NF-1..NF-5 structure and dispositions, NF-6's fix mechanics, the four-PR plan/dependency order, gate scoping, A-012, and the metric schema.
- **§1, §4, §5, §6, §7 and the PR-2/PR-3/PR-4 definitions in §2** — inherited verbatim from v5 (which inherits them from v4).
- **The `_on_inbound_request` change is in `agentic/agentic_layer/layer.py` (agentic, not `PiCN/` core) — no new blast radius, and no AC1/AC3 impact** (AC1/AC3 govern the `PiCN/` forwarded packet format/FIB; the agentic reply terminus does not touch either).

---

## DELTA — the only amended v5 sections follow

> **Everything not appearing below is v5 verbatim.** The sections below **replace** the corresponding v5 text. Line numbers refer to `EXPERIMENT_PLAN_D_E_DESIGN_v5.md`.

---

### §0.1 (AMENDED — NF-9 re-read added)

**§0.1 v5 text stands**, with one appended re-read confirming NF-9:

**Producer side — the response terminus is port-coupled (CORRECTED — NF-9):**
- `AgenticLayer._on_inbound_request` (`layer.py:179-211`) hard-requires a port. Two distinct faults on the port-less path:
  1. `assert self._port is not None` (`layer.py:181`) fires **before** `hub.invoke` (`layer.py:198`). Under `python -O` (asserts stripped) the failure is deferred to line 208 as an `AttributeError`.
  2. `await self._port.send_response(...)` (`layer.py:208`) is **unconditional** — no port means no call.
- The seam that already exists on the port-less stack is `AgenticLayer.queue_to_lower` — the **same** queue NF-6's downward pass-through consumes. `AsyncLayerStack` wires `upper.queue_to_lower = q_to_lower` (`AsyncLayerStack.py:68`) and `lower.queue_from_higher = q_to_lower` (`AsyncLayerStack.py:71`). `AsyncLayerProcess._pump_higher` drains `queue_from_higher` → `data_from_higher` (`AsyncLayerProcess.py:113-116`). So a reply pushed onto `queue_to_lower` reaches NFN's `data_from_higher` with **no port** involved.
- **Conclusion (NF-9):** the response terminus must detect **port-absence** and use the seam; there is no other way for the reply to leave the producer. This is the v1 failure class (spike green, path dead) relocated from the NFN drop (NF-6) to the port assert.

---

### §3.2a (AMENDED — NF-9 added)

**All v5 §3.2a text stands** (NF-2, NF-4, the NF-7 port-less dispatch clause, the single-dispatcher reconciliation). Appended:

**NF-9 — `_on_inbound_request` must be port-independent.** The method currently assumes a port twice; both assumptions are false on the D/E edge forwarder. Required change (`layer.py:179-211`):

- **Selection rule.** `self._port is not None` ⇒ the port-owned path (unchanged behavior: `self._port.send_response`). `self._port is None` ⇒ the **forwarder-backed seam** path: push the reply onto `AgenticLayer.queue_to_lower`.
- **The seam is `queue_to_lower` and nothing else.** It is the **same** `queue_to_lower` NF-6's pass-through consumes (`AsyncLayerStack.py:68/71`; `AsyncLayerProcess.py:113-116`). **No new mechanism, no new queue, no new callback.** `_on_inbound_request` builds the same reply bytes as today and puts `[reply_ref, Content(name, payload)]` on the seam, where `reply_ref` is the delivered inbound face id (NF-4 Option 2 side table) and `name` is the capability name the inbound request arrived on.
- **The reply shape is exactly v5 §3.2b's existing response path** — `Content → queue_to_lower → NFN `handle_from_higher` **NF-6 pass-through** → chunk → timeoutprevention → `ICNLayerCore.handle_content` → `Outbound("lower", [requester_face, content])`. NF-9 reuses it verbatim on the port-less path; it does not define a parallel path.
- **How the layer knows which case it is:** the presence of a port (`self._port is not None`) versus the attachment of the lower queue (`self.queue_to_lower is not None`). No new flag. `AgenticForwarder` leaving `port_adapter=None` is the trigger for the seam case, and the same condition keeps `start_port()`/`_event_loop` from running (NF-7) — so the two are consistent by construction.
- **Relationship to the inline `_dispatch_event` path (NF-7).** NF-9 is the **reply** half; NF-7 is the **request** half. Both are port-less-path changes in `layer.py`. The reply put is a single `await self.queue_to_lower.put(...)` and does **not** break the single-dispatcher guarantee: it originates inside `_dispatch_event` on the same task the stack pump runs.

---

### §3.2b (AMENDED — NF-9 in step 1/2; NF-10 in step 5)

**All v5 §3.2b text stands** (NF-6 Option 1a mechanics, discriminator, sync/async parity, commit plan, 1b fallback). Amended steps:

**Step 1 — unpack (unchanged).** Mirror `NFNLayerCore.py:86-91`: list/tuple of length 2 ⇒ `packet_id = data[0]`, `packet = data[1]`; else default id. **NF-9 note:** have the wrapper pass `has_to_higher=to_higher is not None` and, on the port-less forwarder, `queue_to_lower` **is** the sibling of `queue_from_higher` and **is** `queue_from_higher`, so the reply's `itertools.chain` reaches NFN `data_from_higher` with no port.

**Step 2 — non-ICN-packet guard (unchanged).**

**Step 3 — Interest from higher (unchanged; strict regression guard).** Unchanged: an Interest still routes to the existing `handle_interest`; only `Content` is passed through. **Non-vacuity note for CRAFT:** if `handle_from_lower` uses `has_to_higher=to_higher is not None`, then on the port-less forwarder `to_higher is None` (Agentic has no higher layer), so an Interest from higher would be `"queue_lower"` — i.e. routed **back down**, which is exactly right for the port-less forwarder and is the regression guard that NF-9 journals forwarders (which push replies as **downward** `Content` via `send_response`) are not broken:
- A downward-`Content` journaler is covered by steps 4/5 (actual pass-through).
- A downward/upward-`Interest` journaler stays covered by the step-3 route to `handle_interest`.
- ((**UNVERIFIED / flag** — see §0.2 #6: if a downward `Interest` also arrives in the **stack** case, step 3 must be re-checked so it is not re-routed after the NF-9 change; assertion (b) plus `test_nfn_handle_from_higher_passthrough.py` are the guards.))**

**Step 5 (AMENDED — NF-10, Nack-from-higher asymmetry is deliberate).** `Nack` (and NFN-marked/R2C `Content`) keeps current behavior: no downward pass-through; the NFN computation table owns it. **Explicit NF-10 note:** for a `Content` from higher that is NFN/R2C-marked, this is **correct** (computation-owned). For a `Nack` from higher produced by a **capability backend**, step 5 means the Nack is **silently dropped at NFN**, so the requester only learns of failure via `RequestTimedOut` on the `Context PIT` deadline; it never sees an explicit Nack. **This is deliberately out of scope, not forgotten.** The deadline path already guarantees a terminal outcome (`_on_timeout` → NULL/omission), so no correctness hole; it is a latency/semantics asymmetry only. Pinning the exact `Nack` dispatch (below/above NFN) would widen PR-1's NFN blast radius a second time, so it is deferred. **PR-1 acceptance does not depend on step-5 Nack pass-through**; `test_nack_correlation.py` exercises the requester-side Nack correlation (the timeout path), not an NFN-forwarded Nack.

**Step 6 — direction token choice (unchanged).**

---

### §3.1 assertion (c) (AMENDED — strengthened per NF-9)

Replace v5 §3.1's assertion **(c)** text with:

- **(c)** On a forwarder constructed with `port_adapter=None`, an Interest reaching the agentic layer causes `_on_inbound_request` to be invoked, **the `hub.invoke` spy to fire**, **and the response `Content` to be pushed onto `AgenticLayer.queue_to_lower` (spy on the seam)** — proving the **NF-7 inbound pump and the NF-9 response seam both run with no port**. **(c) must NOT merely assert `hub.invoke` was called.** It must assert the **response `Content` was emitted on the seam** (i.e. the response path ran), matching how (b) proves the pass-through. **(c) is RED on pre-fix code** (assert at `layer.py:181` throws before `hub.invoke`) and **satisfiable** on the port-less forwarder with the NF-9 fix. Run via `python -m pytest agentic/tests/test_producer_path_spike.py::test_c_portless_response_seam -v`.

---

### §2 — AC-PR1 #1(c) (AMENDED — strengthened)

Replace only bullet **(c)** of AC-PR1 #1:

- **(c)** an Interest driven through the forwarder stack results in `_on_inbound_request` being invoked, **`hub.invoke` being called (spy)`, and the response `Content` being pushed onto `AgenticLayer.queue_to_lower` (seam spy)** on a port-less forwarder — **this assertion FAILS on pre-NF-9 code** (the assert at `layer.py:181` throws before `hub.invoke`, so the port-less path is unreachable; and even under `python -O`, line 208 needs a port); it is the acceptance test for the NF-9 fix (§3.1 assertion (c)). Command: `python -m pytest agentic/tests/test_producer_path_spike.py::test_c_portless_response_seam -v`.

---

### §8 (AMENDED — risks + open questions)

**§8.1 adds:**
- **ARCO v5 NF-9 (MAJOR) — `_on_inbound_request` hard-requires a port. CLOSED:** `_on_inbound_request` becomes port-independent; on the port-less forwarder it routes the reply through the existing `queue_to_lower` seam (no new mechanism); on a port-attached node behavior is unchanged. Acceptance = strengthened spike assertion (c) (`test_c_portless_response_seam`) + the existing response tests. **No AC1/AC3 impact** (agentic `layer.py` only, not `PiCN/` core).
- **ARCO v5 NF-10 (MINOR) — Nack-from-higher asymmetry. DOCUMENTED:** a capability-backend Nack from higher is dropped at NFN and surfaces only as `RequestTimedOut`. Deliberately out of scope for PR-1; §3.2b step 5 carries the note, not a code change.

**§8.3 adds:**
- **NEW risk (NF-9 — v1 failure class relocation).** The spike could pass while the port-less response path stays dead if (c) only asserts `hub.invoke`. Mitigation: (c) asserts the seam `Content` push; assertion (b) and `test_send_response_content.py` already cover the NFN pass-through the seam depends on.

**§8.4 adds one decidable item:**
- **(NEW) NF-9 / CFT-6 answer to CRAFT.** The `layer.py:192` inlined reply (`self.queue_to_lower.put([face_id, content])`) is the anticipated NF-9 implementation; the design confirms the seam is `queue_to_lower` and the reply shape is a downward `Content` — no port required, no new queue.

---

## AC-PR1 #1(c) — runnable one-liner (v5.1)

```bash
python -m pytest "agentic/tests/test_producer_path_spike.py::test_c_portless_response_seam" -v
```
RED on pre-fix (assert at `layer.py:181`); GREEN with the NF-9 fix. If test selection by node-id is unverified in this harness, the file-level command `python -m pytest agentic/tests/test_producer_path_spike.py -v` also works (all three assertions). **((Flag: node-id selection is UNVERIFIED — §0.2 #7.))**

## NF-10 note (verbatim, for §3.2b step 5 and §8)

> *A `Nack` from higher (capability backend) is dropped at NFN by step 5; the requester observes failure only via `RequestTimedOut`, never an explicit Nack. The deadline path covers it, so this is **deliberately out of scope, not forgotten**. Deferred to avoid widening PR-1's NFN blast radius. Requester-side Nack correlation timing is exercised by the existing deadline path.*

## Unchanged — explicit statement

**All other v5 content is unchanged.** Exactly and only these are amended: **§0.1** (NF-9 re-read appended), **§3.2a** (NF-9 clause appended), **§3.2b** (NF-9 note in step 1; NF-10 note in step 5), **§3.1 assertion (c)** (strengthened), **§2 AC-PR1 #1(c)** (strengthened), **§8** (NF-9/NF-10 risk + open-question entries). NF-1..NF-5, the four-PR plan, gate scoping, A-012, the metric schema, and the NFN NF-6 fix mechanics are **untouched**. v5.1 supersedes v5 **for the amended sections only**; for everything else v5 (and v4 for §1, §4–§7) stands and is incorporated by reference. **The `_on_inbound_request` change is agentic-only — no AC1/AC3 impact.**

---

## §0.2 (AMENDED — new UNVERIFIED / flagged items)

6. **((UNVERIFIED)) Whether any NFN-marked/R2C `Content` or `Nack` is legitimately delivered from a higher layer as a genuine NFN computation packet.** Step 4's discriminator (packet-type + last-component `b"NFN"` + `r2cclient.R2C_identify_Name`) is the same one `handle_interest` uses (`NFNLayerCore.py:117`), so it is *probably* adequate; the pass-through's safety rests on this discriminator and is guarded by assertion (b) + the parity test. Flagged, not guessed.
7. **((UNVERIFIED)) `pytest --node-id` selection** for the nested `test_c_portless_response_seam`; fall back to the file-level command if selection is unavailable in the branch's pytest config.
8. **((UNVERIFIED — NF-10))** That no capability backend in D/E actually Nacks from higher today (making NF-10 purely latent). If a backend Nacks, the observation is `RequestTimedOut` as stated; the exact latency error introduced is measurable but not measured here.

---

*End of DESIGN v5.1 delta — FINAL, build-ready. No implementation code written — every implementation item is CRAFT's. Every claim cites a verified file:line or is flagged in §0.2. The delta is exactly NF-9 (port-independent response terminus + strengthened assertion (c) + AC-PR1 #1(c)) and NF-10 (Nack-from-higher out-of-scope note). All other v5 content stands unchanged.*