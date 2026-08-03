# ADR-002: Set the process start method explicitly to `fork`

- **Status:** Accepted (temporary — retired by Phase 6)
- **Date:** 2026-08-03
- **Phase:** 1
- **Relates to:** `agent-tasks.md` Task 1.6

## Context

`multiprocessing` chooses a start method by platform. When this codebase was
written, macOS defaulted to `fork`. Modern Python defaults to `spawn` on macOS
and Windows.

Under `spawn`, the child process is a fresh interpreter: every object crossing
the boundary must be **pickled**. Layer objects cannot be.

Measured failure, `PiCN/ProgramLibs/ICNForwarder`:

```
TypeError: cannot pickle 'weakref.ReferenceType' object
  .../multiprocessing/reduction.py:60
```

with a follow-on failure because the process never started:

```
AttributeError: 'NoneType' object has no attribute 'terminate'
  .../multiprocessing/process.py:140
```

That second error is `stop_process()` calling `.terminate()` on a process that
does not exist.

**Verified:** forcing `fork` makes those same 4 tests pass.

Nothing in the codebase calls `set_start_method`, so behaviour depends entirely
on which platform it happens to run on.

## Options

### A — Make layer objects picklable

| Pros | Cons |
|---|---|
| Would work under `spawn`, the modern default | Requires removing weakrefs, sockets, and loggers from every layer's state |
| Portable to Windows | Large, invasive change to code we are about to replace anyway |
| | Effort is discarded entirely at Phase 6 |

### B — Set the start method explicitly to `fork`

| Pros | Cons |
|---|---|
| One small, documented change | `fork` is unavailable on Windows |
| Restores the suite immediately | Python warns: *"process is multi-threaded, use of fork() may lead to deadlocks in the child"* |
| Behaviour stops depending on platform defaults | Temporary — but that is the point |

### C — Leave the default and accept failing tests

| Pros | Cons |
|---|---|
| No change | Phase 1 cannot demonstrate compatibility; the baseline stays broken through every later phase |

## Decision

**Option B.** Set the start method explicitly to `fork` where the platform
supports it, leaving the default elsewhere.

This is explicitly a **bridge**, not a fix. The underlying problem is that the
architecture requires passing unpicklable objects between processes. The asyncio
migration removes process boundaries entirely, at which point this ADR is
retired.

Option A was rejected as effort spent on code scheduled for deletion.

## Consequences

- Phase 1 restores `ProgramLibs` tests without touching layer internals.
- The fork-related `DeprecationWarning` will appear in test output. Expected;
  do not suppress it — it is a standing reminder that this is temporary.
- Windows remains unsupported until asyncio lands, which then improves the
  situation rather than merely preserving it.
- **Phase 6 removes this code.**

## Rules for implementers

1. Use `multiprocessing.set_start_method("fork", force=True)` inside
   `try/except RuntimeError` — it raises if a context already exists.
2. Guard on platform support; do not force `fork` where it is unavailable.
3. Add a comment stating **why**: layer objects are unpicklable, and this is
   removed when asyncio lands. Without that note, a future reader will assume it
   is a preference.
4. **Do not** attempt to make any object picklable. Do not add `__getstate__`,
   `__reduce__`, or remove weakrefs to "solve" this properly.
5. **Do not** suppress the fork/threads `DeprecationWarning`.
6. Do not touch `LayerProcess` or any layer as part of this task.

## Verification

```bash
.venv/bin/python -m pytest PiCN/ProgramLibs/ICNForwarder -q --timeout=90
```

Expect `4 passed`. These fail before this change and pass after.
