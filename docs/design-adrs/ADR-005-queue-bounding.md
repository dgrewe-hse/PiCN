# ADR-005: Inter-layer queues are bounded

- **Status:** Accepted
- **Date:** 2026-08-03
- **Phase:** 2
- **Relates to:** ADR-003, ADR-004

## Context

`multiprocessing.Queue` is effectively unbounded: it buffers in memory and
spills through a feeder thread. A slow layer therefore never applies
backpressure — the producer keeps accepting work and memory grows until
something fails.

`asyncio.Queue` defaults to `maxsize=0`, which is *also* unbounded. Porting
naively preserves the flaw while silently changing its character: under
multiprocessing the buffer was per-process; under asyncio everything shares one
heap.

This is easy to overlook precisely because nothing appears to break. It surfaces
as memory growth under sustained load — exactly the condition a benchmark
creates.

## Options

### A — Unbounded (`maxsize=0`), matching current behaviour

| Pros | Cons |
|---|---|
| Literal behavioural parity | Preserves a real defect |
| No producer ever blocks | Slow consumers cause unbounded memory growth |
| | Removes any signal that a layer is falling behind |

### B — Bounded with blocking `put`

| Pros | Cons |
|---|---|
| Backpressure propagates naturally toward the producer | A full queue can deadlock a cycle of layers |
| Memory is bounded by construction | Changes behaviour versus today |
| A slow layer becomes observable rather than invisible | Requires choosing a size |

### C — Bounded with drop-on-full

| Pros | Cons |
|---|---|
| Never blocks, never grows | Silently discards network packets — unacceptable for a forwarder under test |

## Decision

**Option B: bounded queues with awaiting `put`.** Default `maxsize` is a named
module-level constant, not a literal scattered through the code, and is
overridable per stack for tests and benchmarks.

Option C was rejected outright: silently dropping packets would corrupt every
measurement and every test in ways that look like bugs elsewhere.

## Consequences

- `await queue.put(...)` can now genuinely suspend. Handlers must be written
  expecting that (ADR-003 already makes them coroutines).
- A cycle of layers each blocked on a full queue is a **deadlock**. Layer graphs
  with cycles need care; if one is unavoidable, document it and give that edge a
  larger bound.
- Queue depth becomes a useful diagnostic: a persistently full queue identifies
  the slow layer directly.
- Benchmarks may need a larger bound than the default. That is a configuration
  change, not a code change.

## Rules for implementers

1. Define the default once:
   ```python
   DEFAULT_QUEUE_SIZE = 128   # tune with evidence, not guesswork
   ```
   Do not scatter numeric literals.
2. `LayerStack` accepts an optional queue size and applies it to every queue it
   creates.
3. Always `await queue.put(item)`. **Do not** use `put_nowait` to dodge
   blocking — that reintroduces unbounded growth or silent drops.
4. **Never** catch `asyncio.QueueFull` and discard the item. If you believe an
   item may be dropped, stop and report it.
5. If a benchmark or test needs more headroom, pass a larger size at
   construction. Do not raise the global default to make one case pass.

## Verification

No unbounded queue construction:

```bash
grep -rn "asyncio.Queue()" PiCN/ --include=*.py | grep -v test
```

Expect **empty output** — every construction passes an explicit `maxsize`.

No silent drops:

```bash
grep -rn "put_nowait\|QueueFull" PiCN/ --include=*.py | grep -v test
```

Expect **empty output**.
