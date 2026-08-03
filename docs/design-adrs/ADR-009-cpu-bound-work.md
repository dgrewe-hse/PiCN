# ADR-009: CPU-bound work goes to an executor

- **Status:** Accepted
- **Date:** 2026-08-03
- **Phase:** 4
- **Relates to:** ADR-003, ADR-004

## Context

Under multiprocessing, each layer had its own OS process and therefore its own
GIL. A layer performing heavy computation slowed only itself; other layers kept
forwarding.

Under asyncio (ADR-004) every layer shares one thread and one event loop. A
synchronous computation that runs for *n* milliseconds stalls **every** layer for
*n* milliseconds — including packet forwarding.

This is the single most significant behavioural regression the migration
introduces, and it is invisible until load is applied.

The computation layer is the obvious candidate: `PiCN/Layers/NFNLayer` contains
an executor and a parser, and executes named functions in a sandboxed Python
environment. Function execution time is arbitrary — it is user-supplied
computation.

Other candidates worth checking: content-store operations over large objects,
chunking, and any cryptographic or serialisation work over sizeable payloads.

## Options

### A — Run everything on the event loop

| Pros | Cons |
|---|---|
| Simplest; no executor management | A single slow computation stalls all forwarding |
| | Latency measurements become meaningless under load |
| | Reintroduces, in a new form, exactly the bottleneck the architecture avoids |

### B — Dispatch CPU-bound work to a thread executor

| Pros | Cons |
|---|---|
| Event loop stays responsive | Threads share the GIL, so CPU-bound Python does not truly parallelise |
| `loop.run_in_executor` is a one-line change at the call site | Helps latency, not throughput |
| Works for I/O-bound blocking calls too | |

### C — Dispatch to a process pool executor

| Pros | Cons |
|---|---|
| True parallelism; the GIL does not apply | Arguments and results must be picklable — the exact constraint that broke `spawn` (ADR-002) |
| | Reintroduces process management the migration removes |

## Decision

**Option B by default**, with C available where a specific workload proves both
CPU-bound and picklable.

The immediate objective is that **the event loop must not stall**. A thread
executor achieves that even without true parallelism, because the loop is free
to forward packets while the worker thread computes.

Option C is not adopted wholesale: the picklability constraint is what made
`spawn` fail, and reintroducing it as a default would recreate a solved problem.

## Consequences

- Named-function execution moves off the event loop. Its own latency may rise
  slightly; forwarding latency stops depending on it.
- An executor must be created and shut down with the stack (ADR-006).
- Thread-safety becomes a real concern for anything the executor touches — it no
  longer has the event loop's implicit single-threaded guarantee.
- Which work is actually CPU-bound must be **measured**, not assumed. See the
  rules.

## Rules for implementers

1. Do not dispatch to an executor speculatively. **Measure first**: if a
   synchronous call can exceed ~10 ms, it belongs in an executor; otherwise leave
   it on the loop. Record the measurement in the commit message.
2. Dispatch form:
   ```python
   result = await loop.run_in_executor(self._executor, fn, *args)
   ```
3. The executor is owned by the stack, created at startup and shut down with it.
   **Do not** create an executor per call or per layer.
4. Anything running in the executor must not touch event-loop objects.
   Specifically forbidden inside executor functions: `asyncio.Queue`, any
   coroutine, `loop.*`. Return a value and let the caller do the awaiting.
5. **Do not** use `ProcessPoolExecutor` without first confirming the arguments
   and results are picklable — and record that check.
6. Do not "solve" a slow layer by adding sleeps or yielding with
   `await asyncio.sleep(0)`. That masks the stall rather than removing it.

## Verification

Blocking work is not called directly from a handler:

```bash
grep -rn "run_in_executor" PiCN/ --include=*.py | grep -v test
```

Each hit should correspond to a call site documented as measured CPU-bound.

The event loop is not starved — run the suite with asyncio debug mode, which
warns on callbacks exceeding 100 ms:

```bash
PYTHONASYNCIODEBUG=1 .venv/bin/python -m pytest PiCN/Layers/NFNLayer -q --timeout=120 2>&1 | grep -i "slow\|took"
```

Investigate every warning. Not all are faults, but each needs an explanation.
