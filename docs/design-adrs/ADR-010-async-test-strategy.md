# ADR-010: `pytest-asyncio` in strict mode, function-scoped event loops

- **Status:** Accepted
- **Date:** 2026-08-03
- **Phase:** 2 (adopted) / 7 (completed)
- **Relates to:** ADR-003, ADR-006, ADR-007

## Context

The existing suite is `unittest`-style and synchronous, written for `nose`
(unmaintained) and run under `pytest`. Measured state on Python 3.14:
**467 tests collect**; `PiCN/Packets` and `PiCN/Layers/ICNLayer` give **80
passes**.

Once handlers become coroutines (ADR-003), tests calling them directly must
await them, which needs an event loop per test. Several decisions follow, and
they are worth making once rather than per file:

- Which plugin, and in which mode.
- Event-loop scope: shared across a module, or fresh per test.
- Whether existing synchronous tests must change.

Getting loop scope wrong produces the worst kind of test failure: order-dependent
and intermittent.

## Options

### Event-loop scope

#### A — Session or module scoped

| Pros | Cons |
|---|---|
| Faster; one loop creation | State leaks between tests — a task left running in one test affects the next |
| | Failures become order-dependent and hard to reproduce |

#### B — Function scoped (fresh loop per test)

| Pros | Cons |
|---|---|
| Complete isolation; no leakage | Slightly slower |
| A leaked task fails its own test, not an unrelated one | |
| Failures reproduce in isolation | |

### Plugin mode

#### `asyncio_mode = auto` — coroutine tests run automatically

| Pros | Cons |
|---|---|
| No decorators needed | Silently changes how *every* collected coroutine is treated |
| | A test that should have been marked is run anyway, hiding the omission |

#### `asyncio_mode = strict` — explicit `@pytest.mark.asyncio`

| Pros | Cons |
|---|---|
| Async tests are visibly marked | One decorator per async test |
| A missing mark fails loudly instead of silently passing | |

## Decision

**`pytest-asyncio` in `strict` mode, with function-scoped event loops.**

Both choices favour explicitness and isolation over speed. In a migration whose
entire purpose is proving behaviour was preserved, a test suite that produces
order-dependent results is worse than a slower one.

Existing synchronous tests are **not** converted wholesale. They are updated only
where the code they exercise becomes async.

## Consequences

- `pyproject.toml` carries `asyncio_mode = "strict"` and the loop-scope setting.
- Every async test needs `@pytest.mark.asyncio`.
- A test leaking a running task fails that test, which is the desired signal.
- Shutdown behaviour (ADR-006) becomes directly testable: cancel a stack and
  assert it stopped within the timeout.
- Supervision (ADR-007) becomes testable: inject a failing layer and assert the
  stack surfaces the exception.

## Rules for implementers

1. Configure once, in `pyproject.toml`:
   ```toml
   [tool.pytest.ini_options]
   asyncio_mode = "strict"
   asyncio_default_fixture_loop_scope = "function"
   ```
   Do not override scope in individual test files.
2. Mark every async test with `@pytest.mark.asyncio`. Do not switch to `auto`
   mode to avoid the decorator.
3. **Do not convert a passing synchronous test to async** unless the code it
   exercises became async. Gratuitous conversion loses baseline comparability
   (ADR-001).
4. Always start and stop stacks within the test. A test that leaves a stack
   running has failed even if its assertions passed.
5. Use `--timeout` on every run. An async test that hangs otherwise blocks the
   entire suite.
6. Do not use `time.sleep` in tests. Await the condition, or use
   `asyncio.wait_for`. Sleep-tuned tests are the flakiness this ADR exists to
   prevent.
7. Prefer asserting on observable output — what appears on a queue — over
   inspecting layer internals. Internals change during migration; the contract
   should not.

## Verification

Configuration is present and strict:

```bash
grep -A2 "asyncio_mode" pyproject.toml
```

No sleep-based synchronisation in tests:

```bash
grep -rn "time.sleep" PiCN/ --include=test_*.py
```

Expect **empty output** for newly written tests. Pre-existing occurrences are
recorded in the baseline and removed as their layers are ported.

Suite is order-independent — run it shuffled and compare against the baseline:

```bash
.venv/bin/pip install pytest-randomly
.venv/bin/python -m pytest PiCN/ -q --timeout=60
```

Counts must match the ordered run. A difference means shared state between
tests.
