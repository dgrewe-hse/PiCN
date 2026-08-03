# ADR-003: Layer handlers become `async def` uniformly

- **Status:** Accepted
- **Date:** 2026-08-03
- **Phase:** 2
- **Relates to:** ADR-004, ADR-009

## Context

Every layer implements two handlers:

```python
def data_from_lower(self, to_lower, to_higher, data): ...
def data_from_higher(self, to_lower, to_higher, data): ...
```

Today they are synchronous, called from a blocking run loop. Most bodies are
pure computation over queues; a minority perform real I/O.

Under asyncio the run loop becomes a coroutine, so a choice arises: must every
handler become a coroutine, or only those that actually await something?

## Options

### A — Handlers stay synchronous; the loop calls them directly

| Pros | Cons |
|---|---|
| Smallest diff; most layer bodies unchanged | A handler that later needs I/O cannot await without changing its signature and every caller |
| No `async` keyword churn | Any accidental blocking call stalls the whole event loop with no syntactic warning |

### B — Mixed: `async def` only where a handler awaits

| Pros | Cons |
|---|---|
| Avoids ceremony where nothing is awaited | The call site must know which kind it is dealing with, or inspect at runtime |
| | Converting a handler later is a breaking change to its layer |
| | Mixed contracts are a recurring source of confusion, especially for contributors unfamiliar with asyncio |

### C — All handlers become `async def`

| Pros | Cons |
|---|---|
| One uniform contract; call sites never branch | `async` on bodies that never await looks redundant |
| A handler can start awaiting later with no signature change | Marginal overhead per call |
| Blocking calls become visibly wrong in an `async` body — easier to spot in review | |

## Decision

**Option C.** Both handlers are `async def` on every layer, whether or not the
body awaits.

Uniformity is worth more than avoiding a few redundant keywords. The dominant
risk in this migration is a blocking call left in a handler stalling the entire
event loop — and that risk is reduced when every handler is unambiguously async
and reviewed as such.

## Consequences

- Every layer's two handlers change signature. Mechanical, but touches all of
  them.
- The run loop always awaits handlers; no branching on handler type.
- Queue operations inside handlers become `await queue.put(...)` (see ADR-005 —
  queues are bounded, so `put` can genuinely block).
- Tests calling handlers directly must await them (see ADR-010).

## Rules for implementers

1. Signature exactly:
   ```python
   async def data_from_lower(self, to_lower, to_higher, data) -> None: ...
   async def data_from_higher(self, to_lower, to_higher, data) -> None: ...
   ```
2. Add `async` even when the body awaits nothing. Do not "optimise" it away.
3. **Never call a blocking function inside a handler.** Specifically forbidden:
   `time.sleep`, `socket.recv`, `socket.send`, blocking file I/O,
   `queue.Queue.get`. Use the async equivalent, or ADR-009's executor.
4. Do not change handler *logic* while changing the signature. Signature and
   behaviour changes belong in separate commits.
5. Preserve parameter names — `to_lower` and `to_higher` — so existing call
   sites and tests read unchanged.

## Verification

Every handler is a coroutine function:

```bash
grep -rn "def data_from_lower\|def data_from_higher" PiCN/ --include=*.py | grep -v "async def" | grep -v test
```

Expect **empty output** once Phase 4 completes.

No blocking sleeps remain in layer code:

```bash
grep -rn "time.sleep" PiCN/Layers/ --include=*.py | grep -v test
```

Expect **empty output**.
