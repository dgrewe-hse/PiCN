# ADR-004: `LayerStack` wires `asyncio.Queue` pairs; layers run as tasks

- **Status:** Accepted
- **Date:** 2026-08-03
- **Phase:** 2
- **Relates to:** ADR-003, ADR-005, ADR-006, ADR-007

## Context

`LayerStack` currently wires layers together with `multiprocessing.Queue` pairs
and starts each layer as an OS process. Its public API is worth preserving:

```python
LayerStack([upper, middle, lower])
stack.insert(layer, on_top_of=..., below_of=...)
```

`LayerProcess` carries **three** run loops — `_run_poll` (`select.poll`),
`_run_select` (`select.select`), and `_run_sleep` (a 0.3 s busy-wait for
Windows). Which one runs is chosen partly by `in_unittest()`, which inspects the
call stack for the strings `"unittest"` or `"nose"`.

All three loops reach into `multiprocessing.Queue._reader` — a private
attribute — to obtain file descriptors to select on.

## Options

### A — Keep processes, swap queue types

Not viable: `asyncio.Queue` is not shareable across processes. Choosing asyncio
means choosing one process.

### B — One event loop, layers as `asyncio.Task`, `asyncio.Queue` between them

| Pros | Cons |
|---|---|
| All three run loops collapse into one | Loses true parallelism across layers (see ADR-009) |
| `select`/`poll`, the Windows busy-wait, and `in_unittest()` all become unnecessary | A blocking call anywhere stalls every layer, not just one |
| No private-API dependency | |
| No pickling, so `__getstate__`/`__setstate__` go away | |
| No 1024-descriptor ceiling, so simulations can grow | |

### C — One event loop, but layers as threads with `janus`-style queues

| Pros | Cons |
|---|---|
| Preserves some parallelism | Adds a dependency and thread-safety burden |
| | Keeps two concurrency models in play — the confusion the migration exists to remove |

## Decision

**Option B.** One event loop per node. Each layer runs as an `asyncio.Task`
driving a single `async def run()` loop. Adjacent layers are connected by
`asyncio.Queue` pairs, wired by `LayerStack` exactly as today.

The public `LayerStack` API — construction from an ordered list, and
`insert(on_top_of=/below_of=)` — is preserved. Only the queue type and the
execution primitive change.

## Consequences

- `_run_poll`, `_run_select`, `_run_sleep`, and `in_unittest()` are all deleted
  in Phase 6.
- `import select` disappears from layer code.
- The `os.name == 'nt'` branch goes away; asyncio supports Windows natively,
  which is a capability gain rather than only a simplification.
- Layers no longer have a `.process`; they have a task (see ADR-006).
- CPU-bound work now needs explicit handling (ADR-009).

## Rules for implementers

1. One `async def run()` per layer, replacing all three `_run_*` methods. It
   awaits both inbound queues concurrently — use `asyncio.wait` with
   `FIRST_COMPLETED`, or one task per direction.
2. **Never access `Queue._reader`** or any other private attribute. If you find
   yourself needing a file descriptor, you are solving the wrong problem.
3. Keep the `queue_to_lower` / `queue_from_lower` / `queue_to_higher` /
   `queue_from_higher` attribute names. Layer code and tests reference them.
4. Preserve `LayerStack.insert()` semantics exactly, including its existing
   `TypeError` and `ValueError` behaviour for bad arguments.
5. Do **not** delete the old run loops during Phase 2. They are removed in
   Phase 6, once nothing depends on them.
6. One event loop per node. Do not create nested loops or call
   `asyncio.run()` inside a layer.

## Verification

Stack wiring works and the public API is intact:

```bash
.venv/bin/python -m pytest PiCN/LayerStack -q --timeout=30
```

No private queue access remains after Phase 6:

```bash
grep -rn "_reader" PiCN/ --include=*.py | grep -v test
```

Expect **empty output**.
