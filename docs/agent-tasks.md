# Agent Task Plan — Modernization

Execution plan for AI coding agents, written for **small local models**
(e.g. a 30B-class mixture-of-experts with ~3B active parameters).

Read [`AGENTS.md`](../AGENTS.md) first for repository conventions, and
[`modernization.md`](modernization.md) for why this work exists.

**Before each phase, read that phase's ADRs** in
[`design-adrs/`](design-adrs/README.md). The task plan says *what to do*; the
ADRs say *why, and what not to do instead*. Each ADR ends with binding rules and
a verification command.

| Phase | ADRs to read first |
|---|---|
| 0 | ADR-001 |
| 1 | ADR-002 |
| 2 | ADR-003, ADR-004, ADR-005, ADR-006, ADR-007, ADR-010 |
| 3 | ADR-008 (and its 2026-08-04 addendum), ADR-009 (and its 2026-08-04 addendum) |
| 4 | ADR-003 (and its 2026-08-04 extract-core addendum), ADR-009 (and its 2026-08-04 addendum), ADR-010 |
| 5 | ADR-004 (and its 2026-08-04 shared-builder addendum), ADR-006, ADR-009 |

---

## How to use this document

Each task is **atomic and self-contained**. Do not batch them. Do not read
ahead. Work one task at a time, in order.

Every task has the same five parts:

- **Goal** — one sentence.
- **Files** — the exact paths you may touch. Touch nothing else.
- **Prompt** — copy this verbatim into the agent.
- **Verify** — run this command; it decides success.
- **Expect** — what success looks like. Anything else is failure.

### Rules for the agent

1. **One task per session.** Start a fresh context for each task.
2. **Never skip Verify.** A task is not done until its command produces the
   expected output.
3. **Do not fix unrelated problems.** If you notice something broken outside
   your task's file list, write it down and continue. Do not fix it.
4. **Do not refactor.** These tasks change specific things for specific reasons.
5. **If Verify fails twice, stop** and report what happened. Do not keep trying
   variations.
6. **Do not change network behaviour.** Packet formats, forwarding logic, and
   PIT/FIB/CS semantics must behave identically before and after every task.

### Measured starting state

Probed on Python 3.14 / macOS / pytest 9.1.1. Your numbers should be close; if
they differ a lot, record that in the baseline rather than trying to match these.

| Fact | Value |
|---|---|
| Tests collected | 467, plus 1 collection error (`numpy` missing) |
| `PiCN/Packets` + `PiCN/Layers/ICNLayer` | 80 passed |
| `PiCN/ProgramLibs/ICNForwarder` | 4 failed, 4 errors |
| Cause of those failures | `TypeError: cannot pickle 'weakref.ReferenceType' object` |
| With start method forced to `fork` | Those same 4 tests pass |

---

# Phase 0 — Baseline and safety net

**Purpose:** record exactly what works *before* changing anything. Without this,
"did we break it?" cannot be answered later.

**Do not change any code in Phase 0.** Only add files.

---

> **Read first:** [ADR-001](design-adrs/ADR-001-baseline-first-migration.md) —
> why nothing is fixed during Phase 0.

### Task 0.1 — Create the development environment

**Goal:** a working virtual environment with a test runner.

**Files:** none (creates `.venv/`, which is not committed).

**Prompt:**

```
Create a Python virtual environment in .venv at the repository root, then
install pytest, pytest-timeout, and numpy into it.

Do NOT install anything from requirements.txt — it pins an obsolete
opencv-python that fails to build on modern Python and is only needed for demos
we are not running.

Do NOT run "pip install -e ." — we do not need the package installed.

Commands to run:
  python3 -m venv .venv
  .venv/bin/pip install pytest pytest-timeout numpy

Then report the pytest version.
```

**Verify:**
```bash
.venv/bin/pytest --version
```

**Expect:** a version number, 8.x or newer.

---

### Task 0.2 — Ignore local artifacts

**Goal:** stop test and environment artifacts polluting git.

**Files:** `.gitignore` (create or edit).

**Prompt:**

```
Add these entries to .gitignore at the repository root, creating the file if it
does not exist. Do not remove any existing entries.

  .venv/
  .pytest_cache/
  __pycache__/
  *.pyc
  baseline/

Note: .pytest_cache is currently tracked by git. After updating .gitignore, run:
  git rm -r --cached .pytest_cache

Do not delete the directory from disk, only untrack it.
```

**Verify:**
```bash
git status --short
```

**Expect:** `.pytest_cache` shows as deleted-from-index (`D`), and `.gitignore`
as modified or added. No other files listed.

---

### Task 0.3 — Record the collection baseline

**Goal:** capture which tests exist and which fail to import.

**Files:** creates `baseline/collect.txt`. No source changes.

**Prompt:**

```
Create a directory named baseline at the repository root.

Run this command and save ALL of its output to baseline/collect.txt:
  .venv/bin/python -m pytest --collect-only -q

The command may report errors. That is expected and is exactly what we are
recording. Do not fix anything. Do not modify any test file.

After saving, report: how many tests were collected, and how many collection
errors occurred.
```

**Verify:**
```bash
tail -3 baseline/collect.txt
```

**Expect:** a line reporting collected tests (roughly 467) and possibly errors.

---

### Task 0.4 — Record the full-run baseline

**Goal:** capture pass/fail/error for every test, with a timeout so hangs cannot
block the run.

**Files:** creates `baseline/run-default.txt`. No source changes.

**Prompt:**

```
Run the full test suite with a per-test timeout and save ALL output to
baseline/run-default.txt:

  .venv/bin/python -m pytest -v --timeout=60 -p no:cacheprovider > baseline/run-default.txt 2>&1

This will take several minutes. Many tests are expected to FAIL. That is the
point — we are recording the current state, not fixing it.

Do NOT modify any source or test file. Do NOT try to make failing tests pass.

When it finishes, report the final summary line (the one with counts of passed,
failed, and errors).
```

**Verify:**
```bash
grep -E "passed|failed|error" baseline/run-default.txt | tail -1
```

**Expect:** a summary line with counts. Failures are expected and correct.

---

### Task 0.5 — Record the fork-mode baseline

**Goal:** measure how much of the failure is caused by the default process start
method rather than by real breakage.

**Files:** creates `baseline/run-fork.txt` and a temporary `conftest.py` which is
**deleted afterwards**. No source changes.

**Prompt:**

```
Step 1 — create a file named conftest.py at the repository root containing
exactly this:

import multiprocessing
try:
    multiprocessing.set_start_method("fork", force=True)
except RuntimeError:
    pass

Step 2 — run the suite again, saving all output:

  .venv/bin/python -m pytest -v --timeout=60 -p no:cacheprovider > baseline/run-fork.txt 2>&1

Step 3 — DELETE conftest.py. This is important. It is a measurement, not a fix.

  rm conftest.py

Report the final summary line, and confirm conftest.py no longer exists.
```

**Verify:**
```bash
ls conftest.py 2>&1; grep -E "passed|failed|error" baseline/run-fork.txt | tail -1
```

**Expect:** `ls` reports no such file, and a summary line showing **more passing
tests** than `run-default.txt`.

---

### Task 0.6 — Write the baseline report

**Goal:** a human-readable record that later phases compare against.

**Files:** creates `docs/baseline.md`. No source changes.

**Prompt:**

```
Read these three files:
  baseline/collect.txt
  baseline/run-default.txt
  baseline/run-fork.txt

Create docs/baseline.md containing:

1. A header stating the date, Python version, OS, and pytest version.
2. A table with three rows (collect, default run, fork run) and columns for
   passed, failed, errors, and collection errors.
3. A list of every test file that had failures under the DEFAULT run, one per
   line.
4. A short section titled "Tests that pass under fork but fail under spawn",
   listing the difference between the two runs.
5. A section titled "Known causes", stating only what the output actually shows.
   Do not speculate about causes not visible in the logs.

Use only information present in those three files. Do not guess. Do not add
recommendations.
```

**Verify:**
```bash
grep -c "" docs/baseline.md
```

**Expect:** a non-zero line count, and the file contains the three-row table.

---

### Task 0.7 — Characterization test for the layer contract

**Goal:** lock in current layer behaviour so the asyncio migration can be checked
against it.

**Files:** creates `PiCN/Layers/ICNLayer/test/test_characterization.py` only.

**Prompt:**

```
Read PiCN/Layers/ICNLayer/BasicICNLayer.py to understand its interface,
especially data_from_lower and data_from_higher, and how it uses the queues
passed to those methods.

Create a NEW test file: PiCN/Layers/ICNLayer/test/test_characterization.py

The tests must record CURRENT behaviour, not desired behaviour:
- Send an Interest into data_from_lower with a plain queue, and assert exactly
  what appears on the outgoing queue.
- Do the same for a Content object.
- Do the same for an Interest that matches nothing.

Rules:
- Use unittest.TestCase, matching the style of the existing tests in that
  directory.
- Do NOT use multiprocessing. Use queue.Queue as a stand-in.
- Do NOT modify BasicICNLayer.py or any other existing file.
- If a behaviour looks like a bug, still assert the CURRENT behaviour and add a
  comment saying it looks wrong. Do not fix it.

Every assertion must describe what the code does today.
```

**Verify:**
```bash
.venv/bin/python -m pytest PiCN/Layers/ICNLayer/test/test_characterization.py -v --timeout=30
```

**Expect:** all tests pass. If any fail, the test encodes an assumption rather
than observed behaviour — correct the test, not the source.

---

### Task 0.8 — Commit the baseline

**Goal:** make the baseline permanent and reviewable.

**Files:** `.gitignore`, `docs/baseline.md`, the new characterization test.

**Prompt:**

```
Stage and commit ONLY these:
  .gitignore
  docs/baseline.md
  PiCN/Layers/ICNLayer/test/test_characterization.py
  the .pytest_cache removal from the index

Do NOT commit the baseline/ directory — it is raw output and is gitignored.

Use this commit message:

  Record pre-migration test baseline

  Captures the state of the test suite on current Python before any
  modernization work: collection results, a full run under the default process
  start method, and a run with the start method forced to fork.

  Adds a characterization test for the ICN layer contract so the asyncio
  migration can be verified against current observable behaviour.

  No source code is changed by this commit.

Then show the output of: git status --short
```

**Verify:**
```bash
git status --short && git log --oneline -1
```

**Expect:** a clean working tree apart from ignored files, and the new commit.

---

## Phase 0 exit criteria

Do not start Phase 1 until **all** are true:

- [ ] `docs/baseline.md` exists and records three runs
- [ ] The fork-vs-default difference is documented
- [ ] At least one characterization test passes
- [ ] The baseline is committed
- [ ] No source file was modified in Phase 0

---

# Phase 1 — Python 3.14 compatibility

**Purpose:** run correctly on current Python while still using
`multiprocessing`. This separates "does it work on 3.14" from "does asyncio
work", so a later failure cannot be confused with a version problem.

---

> **Read first:** [ADR-002](design-adrs/ADR-002-process-start-method.md) — why
> `fork` is set explicitly, and why *not* to make objects picklable.

### Task 1.1 — Add `pyproject.toml`

**Goal:** declare build metadata and dev dependencies.

**Files:** creates `pyproject.toml`.

**Prompt:**

```
Create pyproject.toml at the repository root.

Read setup.py first to get the package list, author, and license — reuse them
exactly. Do not invent values.

The file must:
- Use setuptools as the build backend.
- Set requires-python to ">=3.10".
- Declare an optional dependency group named "dev" containing: pytest,
  pytest-timeout.
- Declare an optional dependency group named "demos" containing opencv-python
  with NO version pin.
- Have NO required runtime dependencies.
- Include a [tool.pytest.ini_options] section setting testpaths to "PiCN".

Do NOT delete setup.py. Do NOT change it.
```

**Verify:**
```bash
.venv/bin/python -c "import tomllib;tomllib.load(open('pyproject.toml','rb'));print('valid')"
```

**Expect:** `valid`.

---

### Task 1.2 — Remove the dead `nose` configuration

**Goal:** stop configuring a test runner that no longer works.

**Files:** `setup.cfg` only.

**Prompt:**

```
Open setup.cfg. Delete the entire [nosetests] section, including every line
under it.

Keep the [metadata] section exactly as it is.

Do not change any other file.
```

**Verify:**
```bash
grep -c nosetests setup.cfg
```

**Expect:** `0`.

---

### Task 1.3 — Fix invalid escape sequences

**Goal:** remove `SyntaxWarning`s that will become errors in future Python.

**Files:** only files reported by the command below.

**Prompt:**

```
Run this to find the affected files:
  .venv/bin/python -W error::SyntaxWarning -m compileall -q PiCN/ 2>&1 | grep "invalid escape"

For EACH file reported, fix ONLY the invalid escape sequences. Two valid fixes:
- Add an "r" prefix to make the string raw:  "\ "  ->  r"\ "
- Double the backslash:                      "\ "  ->  "\\ "

Prefer the raw-string prefix for ASCII-art and docstrings.

Rules:
- Change ONLY the string literals that produce the warning.
- Do NOT reformat, reindent, or otherwise edit these files.
- Do NOT touch files not in the reported list.
```

**Verify:**
```bash
.venv/bin/python -W error::SyntaxWarning -m compileall -q PiCN/ 2>&1 | grep -c "invalid escape"
```

**Expect:** `0`.

---

### Task 1.4 — Fix `is` comparisons against literals

**Goal:** correct latent bugs that work only by accident of string interning.

**Files:** `PiCN/Mgmt/Mgmt.py` and any others reported.

**Prompt:**

```
Run this to find them:
  .venv/bin/python -W error::SyntaxWarning -m compileall -q PiCN/ 2>&1 | grep "with 'str' literal\|with 'int' literal"

Known locations: PiCN/Mgmt/Mgmt.py line 42 and line 182.

For each, replace the identity comparison with an equality comparison:
  x is 'text'      ->  x == 'text'
  x is not 'text'  ->  x != 'text'
  x is 5           ->  x == 5

This is a real bug fix: "is" compares object identity, and it only appears to
work because Python sometimes reuses short string objects.

Change ONLY those comparison operators. Nothing else.
```

**Verify:**
```bash
.venv/bin/python -W error::SyntaxWarning -m compileall -q PiCN/ 2>&1 | grep -c "literal"
```

**Expect:** `0`.

---

### Task 1.5 — Replace deprecated `setDaemon()`

**Goal:** remove a deprecated threading call.

**Files:** `PiCN/Layers/ICNLayer/BasicICNLayer.py` and any others reported.

**Prompt:**

```
Find every use:
  grep -rn "setDaemon" PiCN/

Known location: PiCN/Layers/ICNLayer/BasicICNLayer.py around line 225.

Replace each call with the attribute form:
  t.setDaemon(True)   ->   t.daemon = True

Behaviour is identical; only the deprecated method call changes.

Change nothing else.
```

**Verify:**
```bash
grep -rc "setDaemon" PiCN/ | grep -v ":0" | wc -l
```

**Expect:** `0`.

---

### Task 1.6 — Make the process start method explicit

**Goal:** stop inheriting a platform default that breaks the suite.

**Files:** `PiCN/Processes/__init__.py` or a new `PiCN/Processes/startmethod.py`.
Read the existing `__init__.py` before choosing.

**Prompt:**

```
Background: on modern macOS and Windows, multiprocessing defaults to the "spawn"
start method. This codebase was written when "fork" was the default. Under
spawn, layer objects must be pickled to reach the child process, and they
contain objects that cannot be pickled — which is why many tests fail.

Task: add an explicit, documented start-method selection so behaviour no longer
depends on the platform default.

Requirements:
- Provide a function that sets the start method to "fork" where the platform
  supports it, and leaves the default otherwise.
- Use multiprocessing.set_start_method(..., force=True) inside a try/except
  RuntimeError, because it raises if the context is already set.
- Add a comment explaining WHY fork is required today: layer objects are not
  picklable, and this constraint disappears when the asyncio migration lands.
- Call it from PiCN/Processes/__init__.py so it applies on import.

Do NOT change LayerProcess or any layer. Do NOT try to make objects picklable.
```

**Verify:**
```bash
.venv/bin/python -m pytest PiCN/ProgramLibs/ICNForwarder -q --timeout=90
```

**Expect:** `4 passed`. These fail before this task and pass after.

---

### Task 1.7 — Teach `in_unittest()` about pytest

**Goal:** stop the run-loop selection depending on a stale heuristic.

**Files:** `PiCN/Processes/LayerProcess.py` only.

**Prompt:**

```
Open PiCN/Processes/LayerProcess.py and find the in_unittest() method.

It inspects the call stack looking for the strings "unittest" or "nose" to
decide which run loop to use. It does not know about pytest, so under pytest it
returns False and selects a different loop than intended.

Add "pytest" to the strings it looks for, alongside "unittest" and "nose".

Change ONLY that check. Do not restructure the method. Do not touch the run
loops.
```

**Verify:**
```bash
.venv/bin/python -m pytest PiCN/Layers/ICNLayer -q --timeout=30
```

**Expect:** same pass count as the baseline, or better.

---

### Task 1.8 — Compare against the baseline

**Goal:** prove Phase 1 changed compatibility, not behaviour.

**Files:** creates `baseline/run-phase1.txt`, updates `docs/baseline.md`.

**Prompt:**

```
Run the suite and save all output:

  .venv/bin/python -m pytest -v --timeout=60 -p no:cacheprovider > baseline/run-phase1.txt 2>&1

Then compare baseline/run-phase1.txt against baseline/run-default.txt and
baseline/run-fork.txt.

Add a new section to docs/baseline.md titled "After Phase 1" containing:
- The new pass/fail/error counts.
- Any test that passed before and fails now. THIS IS THE IMPORTANT ONE — list
  every such test explicitly.
- Any test that failed before and passes now.

If ANY test regressed (passed before, fails now), stop and report it clearly.
Do not attempt to fix it in this task.
```

**Verify:**
```bash
grep -A5 "After Phase 1" docs/baseline.md
```

**Expect:** the new section, showing pass counts at least matching the fork
baseline and **no regressions**.

---

## Phase 1 exit criteria

- [ ] `pyproject.toml` exists and parses
- [ ] No `SyntaxWarning` during collection
- [ ] `ProgramLibs/ICNForwarder` tests pass
- [ ] Results equal or better than the fork baseline, with **zero regressions**
- [ ] `docs/baseline.md` has an "After Phase 1" section

---

# Phase 2 — Async foundations

**Purpose:** introduce the async execution model **alongside** the existing
`multiprocessing` one, without migrating any production layer onto it yet. This
separates "does the new foundation work, in isolation, with dummy layers" from
"does migrating a real layer preserve behaviour" (Phase 4), so a failure cannot
be attributed to the wrong cause.

**Do not touch any existing file in Phase 2.** `LayerProcess.py`, `LayerStack.py`,
and every real layer keep working exactly as before, unchanged. Everything in
this phase is new, additive files. Per ADR-004 rule 5, the old run loops are
deleted in Phase 6, not now.

---

> **Read first:** [ADR-003](design-adrs/ADR-003-handler-lifecycle.md),
> [ADR-004](design-adrs/ADR-004-async-layerstack.md),
> [ADR-005](design-adrs/ADR-005-queue-bounding.md),
> [ADR-006](design-adrs/ADR-006-shutdown-cancellation.md),
> [ADR-007](design-adrs/ADR-007-error-propagation.md),
> [ADR-010](design-adrs/ADR-010-async-test-strategy.md) — all six govern this
> phase and each ends with binding rules. Do not improvise past what they
> specify; where a rule gives an exact code shape, use it verbatim.

### Task 2.1 — Configure `pytest-asyncio`

**Goal:** adopt the async test tooling before any async code exists to test it.

**Files:** `pyproject.toml`, `.github/workflows/ci.yml`.

**Prompt:**

```
Read docs/design-adrs/ADR-010-async-test-strategy.md first.

In pyproject.toml:
- Add "pytest-asyncio" to the "dev" optional-dependency group, alongside the
  existing pytest, pytest-timeout, pytest-rerunfailures.
- In the existing [tool.pytest.ini_options] section, add exactly:
    asyncio_mode = "strict"
    asyncio_default_fixture_loop_scope = "function"

In .github/workflows/ci.yml:
- Add pytest-asyncio to the "pip install" line in the "Install test
  dependencies" step, alongside the existing packages.

Do not change asyncio_mode to "auto". Do not set any other loop-scope option.
Do not touch any other file.
```

**Verify:**
```bash
pip show pytest-asyncio | head -1 && grep -A2 "asyncio_mode" pyproject.toml && grep "pytest-asyncio" .github/workflows/ci.yml
```

**Expect:** `pytest-asyncio` reports installed (install it locally first if
needed: `pip install pytest-asyncio`), `asyncio_mode = "strict"` and
`asyncio_default_fixture_loop_scope = "function"` both present, and the CI
install line includes `pytest-asyncio`.

---

### Task 2.2 — `AsyncLayerProcess`: async handlers and cancellation-safe lifecycle

**Goal:** the async analogue of `LayerProcess` — async handlers, a single run
loop, and a `start()`/`stop()` lifecycle that follows ADR-006 exactly. No
`LayerStack` wiring yet; this is tested standalone with plain `asyncio.Queue`s.

**Files:** creates `PiCN/Processes/AsyncLayerProcess.py` and
`PiCN/Processes/test/test_AsyncLayerProcess.py` only.

**Prompt:**

```
Read PiCN/Processes/LayerProcess.py and PiCN/Processes/PiCNProcess.py first, to
match attribute names and docstring style. Then read
docs/design-adrs/ADR-003-handler-lifecycle.md and
docs/design-adrs/ADR-006-shutdown-cancellation.md in full.

Create PiCN/Processes/AsyncLayerProcess.py, a NEW file, containing:

1. A module constant:
     SHUTDOWN_TIMEOUT = 5.0  # seconds. See ADR-006 "Rules for implementers", #6.

2. class AsyncLayerProcess(abc.ABC):
   - __init__(self, logger_name="AsyncLayerProcess", log_level=255): sets up
     self.logger = Logger(logger_name, log_level) directly (do NOT inherit from
     PiCNProcess — its __getstate__/__setstate__ pickling support exists only
     for multiprocessing and is unnecessary here; see AGENTS.md). Initialise
     queue_from_lower, queue_from_higher, queue_to_lower, queue_to_higher as
     Optional[asyncio.Queue] attributes with the SAME property names as
     LayerProcess (getters/setters), and self._task: Optional[asyncio.Task] =
     None.
   - Abstract methods, signature EXACTLY:
       async def data_from_lower(self, to_lower, to_higher, data) -> None
       async def data_from_higher(self, to_lower, to_higher, data) -> None
   - async def run(self) -> None: the single run loop, replacing LayerProcess's
     three _run_* variants. Implement it as one task per direction, per
     ADR-004 rule 1, using asyncio.wait(FIRST_EXCEPTION) rather than
     asyncio.TaskGroup:

       async def run(self) -> None:
           async def _pump_lower() -> None:
               while True:
                   data = await self.queue_from_lower.get()
                   await self.data_from_lower(self.queue_to_lower, self.queue_to_higher, data)

           async def _pump_higher() -> None:
               while True:
                   data = await self.queue_from_higher.get()
                   await self.data_from_higher(self.queue_to_lower, self.queue_to_higher, data)

           tasks = []
           if self.queue_from_lower is not None:
               tasks.append(asyncio.create_task(_pump_lower()))
           if self.queue_from_higher is not None:
               tasks.append(asyncio.create_task(_pump_higher()))
           if not tasks:
               return

           try:
               done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
           except asyncio.CancelledError:
               # asyncio.wait() does NOT cancel the tasks it waits on -- that
               # is run()'s job when run() itself is cancelled (ADR-006).
               for t in tasks:
                   t.cancel()
               await asyncio.wait(tasks)
               raise

           for t in pending:
               t.cancel()
           if pending:
               await asyncio.wait(pending)
           for t in done:
               if t.cancelled():
                   continue
               exc = t.exception()
               if exc is not None:
                   raise exc

     IMPORTANT: do NOT nest the two pumps in an asyncio.TaskGroup instead.
     TaskGroup wraps every child exception in an ExceptionGroup -- even a
     single one -- which would hide the original exception type from Task
     2.4's stack-level supervision and break its "assert it IS that exception,
     not wrapped" requirement below. asyncio.wait(FIRST_EXCEPTION) propagates
     the raw exception unchanged.
   - def start(self) -> asyncio.Task: idempotent — if self._task is None or
     already done, create it with self._task = asyncio.create_task(self.run(),
     name=...) using the logger name. Always return self._task. Must be called
     from within a running event loop.
   - async def stop(self, timeout: float = SHUTDOWN_TIMEOUT) -> None: EXACTLY
     the pattern in ADR-006's "Rules for implementers" #1 — cancel self._task,
     await it with asyncio.wait_for(timeout=timeout), catching
     (asyncio.CancelledError, TimeoutError). If self._task is None, return
     immediately (no-op — ADR-006 rule #4). Set self._task = None in a finally
     block so a stopped layer can be started again.

Create PiCN/Processes/test/test_AsyncLayerProcess.py, matching test_LayerProcess.py's
test cases and naming but NOT its unittest.TestCase base class: use a plain
pytest test class (or module-level test functions) with @pytest.mark.asyncio
on every async test. Read ADR-010's "Addendum" section first —
pytest-asyncio's marker has no effect on unittest.TestCase methods; it silently
reports the test as passed without ever running its body. If you use a class,
name it with a capital "Test" prefix (e.g. TestAsyncLayerProcess), NOT this
repo's usual lowercase test_ClassName — a bare class named test_Foo silently
collects zero tests (see the Addendum for why). Use setup_method/teardown_method
in place of setUp/tearDown.
Define one minimal concrete subclass of AsyncLayerProcess for testing (e.g. one
that echoes data onto the opposite queue, mirroring LayerMock). Cover:
- An item placed on queue_from_lower is dispatched to data_from_lower with the
  correct to_lower/to_higher arguments; likewise for queue_from_higher.
- stop() on a layer that was never started is a no-op (returns promptly,
  raises nothing).
- start() called twice while running returns the SAME task (idempotent), and
  does not create a second run loop.
- Calling stop() actually stops the loop: after stop() returns, the task is
  done, and a further item placed on the queue is never processed (assert
  within a short asyncio.wait_for, not time.sleep — ADR-010 rule #6).

Do not modify LayerProcess.py, PiCNProcess.py, or any other existing file.
```

**Verify:**
```bash
python -m pytest PiCN/Processes/test/test_AsyncLayerProcess.py -v --timeout=30
```

**Expect:** all tests pass.

---

### Task 2.3 — `AsyncLayerStack`: bounded queue wiring

**Goal:** the async analogue of `LayerStack` — same public shape
(`AsyncLayerStack(layers)`, `insert(layer, on_top_of=/below_of=)`), but wiring
bounded `asyncio.Queue`s between `AsyncLayerProcess` instances instead of
`multiprocessing.Queue`s. No start/stop or failure supervision yet — that is
Task 2.4.

**Files:** creates `PiCN/LayerStack/AsyncLayerStack.py` and
`PiCN/LayerStack/test/test_AsyncLayerStack.py` only.

**Prompt:**

```
Read PiCN/LayerStack/LayerStack.py in full first — reuse its construction and
insert() logic almost exactly, only changing the queue type. Then read
docs/design-adrs/ADR-004-async-layerstack.md and
docs/design-adrs/ADR-005-queue-bounding.md in full.

Create PiCN/LayerStack/AsyncLayerStack.py, a NEW file, containing:

1. A module constant, comment kept VERBATIM (ADR-005's provisional value has no
   measurement behind it yet — do not change the number or drop the comment):
     # PROVISIONAL — not derived from measurement. See ADR-005 "Choosing the bound".
     # Replace once per-queue depth has been measured under a representative run.
     DEFAULT_QUEUE_SIZE = 128

2. class AsyncLayerStack, mirroring LayerStack.__init__ and insert() exactly,
   with these changes:
   - Every queue is asyncio.Queue(maxsize=queue_size), never
     asyncio.Queue() with no maxsize (ADR-005 rule 1/3). Accept an optional
     queue_size: int = DEFAULT_QUEUE_SIZE constructor parameter and apply it to
     every queue the stack creates, including inside insert()'s internal
     __insert() helper — thread the value through, do not hardcode it twice.
   - Layers are typed as List[AsyncLayerProcess], not List[LayerProcess].
   - Where LayerStack.insert() raises multiprocessing.ProcessError for
     "already started", raise RuntimeError instead (there is no process
     concept here) with the same message. Preserve the existing TypeError and
     ValueError behaviour for bad on_top_of/below_of arguments EXACTLY
     (ADR-004 rule 4) — do not change when they are raised or their messages.
   - Do NOT implement start_all()/stop_all() yet — leave that for Task 2.4.
     A private self.__started flag guarding insert() is still needed; set it
     to False for now (Task 2.4 will set it in start_all()). Leave a
     "# set by start_all(), Task 2.4" comment where it belongs.
   - Do NOT implement close_all() — asyncio.Queue needs no explicit close.

Create PiCN/LayerStack/test/test_AsyncLayerStack.py, matching test_LayerStack.py's
test cases but NOT its unittest.TestCase base class: use a plain pytest test
class (or module-level test functions) with @pytest.mark.asyncio on every
async test (see ADR-010's "Addendum" — pytest-asyncio silently no-ops async
unittest.TestCase methods instead of running them). If you use a class, name it
with a capital "Test" prefix (e.g. TestAsyncLayerStack), NOT this repo's usual
lowercase test_ClassName — a bare class named test_Foo silently collects zero
tests. Reuse the concrete
AsyncLayerProcess subclass pattern from Task 2.2 (a
tiny dummy layer) to build 2- and 3-layer stacks. Cover:
- Construction wires queue_to_lower/queue_from_lower/queue_to_higher/
  queue_from_higher correctly between adjacent layers, and the top/bottom
  layers get the stack's own outer queues — same assertions as
  test_LayerStack.py's construction tests, adapted to asyncio.Queue.
- insert(on_top_of=...) and insert(below_of=...) both work and preserve
  ordering, same as the existing LayerStack tests.
- insert() still raises TypeError for layer=None and for on_top_of+below_of
  both given or both omitted; ValueError for a reference layer not in the
  stack.
- Every queue the stack creates has the configured maxsize (default 128, and a
  custom value passed to the constructor) — assert via queue.maxsize.
- A queue that is full: awaiting put() on it blocks (does not raise, does not
  drop) until a get() drains it. Use asyncio.wait_for with a short timeout to
  prove the put() is genuinely pending, then drain and confirm it completes.

Do not modify LayerStack.py or any other existing file.
```

**Verify:**
```bash
python -m pytest PiCN/LayerStack/test/test_AsyncLayerStack.py -v --timeout=30 && grep -rn "asyncio.Queue()" PiCN/LayerStack/AsyncLayerStack.py
```

**Expect:** all tests pass, and the `grep` for unbounded `asyncio.Queue()`
construction returns **empty** — every construction in the new file passes an
explicit `maxsize`.

---

### Task 2.4 — Supervised start/stop: failures surface, siblings stop

**Goal:** add `start_all()`/`stop_all()` to `AsyncLayerStack`, with every layer
task supervised per ADR-007 — an unhandled exception in one layer's `run()`
cancels every other layer and is recorded where a caller can observe it.

**Files:** `PiCN/LayerStack/AsyncLayerStack.py`,
`PiCN/LayerStack/test/test_AsyncLayerStack.py` only (both created in Task 2.3).

**Prompt:**

```
Read docs/design-adrs/ADR-007-error-propagation.md and
docs/design-adrs/ADR-006-shutdown-cancellation.md in full before starting.

In PiCN/LayerStack/AsyncLayerStack.py, add to the existing AsyncLayerStack
class (do not restructure what Task 2.3 built):

- self.exception: Optional[BaseException] = None in __init__ — the first
  unhandled layer exception, once one occurs. self._tasks: List[asyncio.Task]
  = [] in __init__.
- def start_all(self) -> None: sets self.__started = True (the flag Task 2.3
  left in place), then for every layer calls task = layer.start() (from Task
  2.2's AsyncLayerProcess), appends it to self._tasks, and attaches
  task.add_done_callback(self._on_layer_done). This satisfies ADR-007 rule #1:
  every create_task happens inside AsyncLayerProcess.start(), whose result is
  retained here AND given a done-callback — never a bare, discarded
  create_task().
- def _on_layer_done(self, task: asyncio.Task) -> None: if task.cancelled(),
  return (cancellation is normal shutdown, ADR-006 — not an error). Otherwise
  read exc = task.exception(). If exc is None, return (the layer's run()
  returned normally — should not normally happen, but is not itself a
  failure). If exc is not None: log it with the full traceback (use
  self.logger.error(..., exc_info=exc) or equivalent — NOT str(exc), per
  ADR-007 rule #4), record self.exception = exc if this is the first failure
  seen, and cancel every task in self._tasks that is not already done (this is
  the "siblings stop" half of ADR-007).
- async def stop_all(self, timeout: float = SHUTDOWN_TIMEOUT) -> None: cancel
  every task in self._tasks, then await them all with a single bound: wrap
  asyncio.gather(*self._tasks, return_exceptions=True) in
  asyncio.wait_for(..., timeout=timeout), catching TimeoutError. A no-op if
  self._tasks is empty (never started). Import SHUTDOWN_TIMEOUT from
  PiCN.Processes.AsyncLayerProcess — do not redefine the constant here.

Never write "except Exception" anywhere in this file — task.exception() reads
the result without needing a try/except (ADR-007 rule #6 — only narrow,
documented, recoverable cases get a try/except at all).

In PiCN/LayerStack/test/test_AsyncLayerStack.py, add tests for:
- A normal 2-3 layer dummy stack: start_all(), push data in at the top,
  observe it (transformed as the dummy layers define) at the bottom, then
  stop_all() and confirm it returns within SHUTDOWN_TIMEOUT and every task in
  self._tasks is done.
- A dummy layer whose data_from_lower raises a distinct, recognisable
  exception: after start_all() and pushing the triggering data, await
  (with asyncio.wait_for, not time.sleep) until stack.exception is set; assert
  it IS that exception (not wrapped or stringified), and assert every OTHER
  layer's task is now cancelled.
- stop_all() before start_all() is a no-op (returns immediately, no error).

Do not modify AsyncLayerProcess.py or any other existing file.
```

**Verify:**
```bash
python -m pytest PiCN/LayerStack/test/test_AsyncLayerStack.py -v --timeout=30 && grep -rn "except Exception" PiCN/LayerStack/AsyncLayerStack.py PiCN/Processes/AsyncLayerProcess.py; grep -n "create_task" PiCN/LayerStack/AsyncLayerStack.py PiCN/Processes/AsyncLayerProcess.py
```

**Expect:** all tests pass; the `except Exception` grep returns **empty**;
every `create_task` hit is immediately assigned to a variable (`task =` or
`self._task =`), never called bare.

---

### Task 2.5 — Verification sweep and baseline update

**Goal:** confirm Phase 2's new files satisfy every governing ADR's own
verification command, and that the existing suite is completely unaffected —
Phase 2 adds code, it does not change behaviour anywhere else.

**Files:** updates `docs/baseline.md` only. No source changes.

**Prompt:**

```
Run each of these and record the output; all must come back clean:

  grep -rn "asyncio.Queue()" PiCN/Processes/AsyncLayerProcess.py PiCN/LayerStack/AsyncLayerStack.py
  grep -rn "put_nowait\|QueueFull" PiCN/Processes/AsyncLayerProcess.py PiCN/LayerStack/AsyncLayerStack.py
  grep -n "terminate()\|time.sleep" PiCN/Processes/AsyncLayerProcess.py PiCN/LayerStack/AsyncLayerStack.py
  grep -n -A3 "except asyncio.CancelledError" PiCN/Processes/AsyncLayerProcess.py PiCN/LayerStack/AsyncLayerStack.py
  grep -rn "def data_from_lower\|def data_from_higher" PiCN/Processes/AsyncLayerProcess.py | grep -v "async def"

All five must produce EMPTY output (the fourth one's context lines don't
count as a violation as long as no matched block is missing "raise" — inspect
by eye, since grep can't verify control flow).

Then run the full suite exactly as Phase 1's baseline did:

  python -m pytest -v --timeout=90 -p no:cacheprovider > /tmp/phase2-run.txt 2>&1
  tail -5 /tmp/phase2-run.txt

Compare the pass/fail/error counts against the "After Phase 1" section of
docs/baseline.md. The counts must match EXACTLY except for the new tests added
in Tasks 2.1-2.4 (which add passes, nothing else). Any change to a previously
passing or previously failing test outside PiCN/Processes/test/
test_AsyncLayerProcess.py and PiCN/LayerStack/test/test_AsyncLayerStack.py is a
regression — stop and report it, do not fix it in this task.

Add a new section to docs/baseline.md titled "After Phase 2" containing:
- The five grep results (or "empty" for each).
- The new pass/fail/error counts and the delta versus "After Phase 1".
- Explicit confirmation that no existing test's outcome changed.
```

**Verify:**
```bash
grep -A10 "After Phase 2" docs/baseline.md
```

**Expect:** the new section, all five grep checks reported empty, and no
regressions versus "After Phase 1".

---

### Task 2.6 — Commit Phase 2

**Goal:** land the async foundations as a single, reviewable, additive commit.

**Files:** `pyproject.toml`, `.github/workflows/ci.yml`,
`PiCN/Processes/AsyncLayerProcess.py`,
`PiCN/Processes/test/test_AsyncLayerProcess.py`,
`PiCN/LayerStack/AsyncLayerStack.py`,
`PiCN/LayerStack/test/test_AsyncLayerStack.py`, `docs/baseline.md`.

**Prompt:**

```
Confirm git status --short shows ONLY the files listed above (plus the usual
ignored artifacts). If anything else changed, stop and report it — Phase 2
must not touch existing production or test files.

Stage exactly those files and commit with this message:

  Phase 2: async foundations (AsyncLayerProcess, AsyncLayerStack)

  Introduces the asyncio execution model alongside the existing
  multiprocessing one, per ADR-003 through ADR-007 and ADR-010. Nothing in
  production uses it yet -- no existing layer, ProgramLib, or test changes
  behaviour. Verified via the new unit/integration tests plus a full-suite
  regression run recorded in docs/baseline.md ("After Phase 2").

  AsyncLayerProcess (PiCN/Processes/): async data_from_lower/data_from_higher
  handlers, a single run() loop replacing the three _run_* variants, and a
  cancel-then-await start()/stop() lifecycle with no terminate() or sleep().

  AsyncLayerStack (PiCN/LayerStack/): wires bounded asyncio.Queue pairs
  between layers, preserving the existing construction and insert() API
  shape. Every layer task is supervised: an unhandled exception cancels its
  siblings and surfaces on stack.exception rather than failing silently.

  Old LayerProcess/LayerStack are untouched and keep running every real
  layer; they are removed in Phase 6 once nothing depends on them.

Then show: git status --short && git log --oneline -1
```

**Verify:**
```bash
git status --short && git log --oneline -1
```

**Expect:** a clean working tree apart from ignored files, and the new commit.

---

## Phase 2 exit criteria

Do not start Phase 3 until **all** are true:

- [ ] `AsyncLayerProcess` and `AsyncLayerStack` exist, each with passing tests
- [ ] No production layer, `ProgramLibs` node, or existing test references
      either new class — Phase 2 is purely additive
- [ ] All five ADR-005/006/007 grep verifications return clean against the two
      new files
- [ ] `docs/baseline.md` has an "After Phase 2" section showing the full suite
      unaffected (identical outcomes outside the two new test files)
- [ ] `pytest-asyncio` runs in strict mode with function-scoped loops

---

# Phase 3 — I/O boundary: `BaseInterface`, `UDP4Interface`, `BasicLinkLayer`

**Purpose:** move the network I/O boundary onto asyncio-native primitives —
`loop.create_datagram_endpoint` instead of blocking sockets, pushed queues
instead of `select()`/`file_descriptor` multiplexing — without a flag-day
cutover of every `ProgramLib` that depends on `BasicLinkLayer` today.

**Unlike Phase 2, this is not purely additive.** `BasicLinkLayer` is
production code every `ProgramLib` already uses. Phase 3 solves that with an
*injected run strategy* rather than a parallel class hierarchy — see
[ADR-008](design-adrs/ADR-008-baseinterface-contract.md)'s 2026-08-04
addendum for the full reasoning. In one sentence: every `LayerProcess`
already runs in its own forked OS process, so the new asyncio engine can live
entirely inside `BasicLinkLayer`'s process without anything outside it (the
old `LayerStack`, `ICNForwarder`, sibling layers) changing shape — as long as
`BasicLinkLayer` still defaults to today's exact behaviour until a
`ProgramLib` explicitly opts in.

**Do not touch `LayerStack.py`, `LayerProcess.py`, `FaceIDTable/`, or
`AddressInfo`.** Their contracts are unaffected (ADR-008 rule 8) and Phase 3
does not depend on Phase 5's node-assembly work.

---

> **Read first:** [ADR-008](design-adrs/ADR-008-baseinterface-contract.md)
> (including its 2026-08-04 addendum — the *original* ADR text describes an
> in-place breaking change; the addendum is what actually governs this phase)
> and [ADR-009](design-adrs/ADR-009-cpu-bound-work.md)'s 2026-08-04 addendum
> (the temporary, narrowly-scoped executor exception this phase needs before
> a real `AsyncLayerStack` exists to own one properly).

### Task 3.1 — `LinkLayerRunStrategy` seam: extract, do not rewrite

**Goal:** give `BasicLinkLayer` an injected run strategy with **zero**
behavioural change — prove the seam itself is safe before anything new is
built on it.

**Files:** creates `PiCN/Layers/LinkLayer/RunStrategy.py`. Modifies
`PiCN/Layers/LinkLayer/BasicLinkLayer.py` only to delegate to it.

**Prompt:**

```
Read PiCN/Layers/LinkLayer/BasicLinkLayer.py in full, and ADR-008's
2026-08-04 addendum in full, before starting.

Create PiCN/Layers/LinkLayer/RunStrategy.py containing:

1. class LinkLayerRunStrategy(abc.ABC): one abstract method,
     def start(self, layer: "BasicLinkLayer") -> None
   Do not add a stop() yet -- Task 3.1 does not change stop_process().

2. class SyncRunStrategy(LinkLayerRunStrategy): start() does EXACTLY what
   BasicLinkLayer.start_process() does today:
     layer.process = multiprocessing.Process(target=layer._run, args=[
         layer._queue_from_lower, layer._queue_from_higher,
         layer._queue_to_lower, layer._queue_to_higher])
     layer.process.daemon = True
     layer.process.start()
   Copy this verbatim from the current start_process() body. Do not
   simplify, rename variables, or "clean up" anything -- this is a pure
   extraction.

In BasicLinkLayer.py:
- Add run_strategy: LinkLayerRunStrategy = None as a constructor parameter,
  defaulting to SyncRunStrategy() when None is passed (so every existing
  caller -- every ProgramLib -- is completely unaffected without changing a
  single call site).
- Store it as self._run_strategy.
- Replace start_process()'s body with: self._run_strategy.start(self)
- Do NOT change stop_process(), _run, _run_poll, _run_select, _run_sleep,
  data_from_lower, data_from_higher, or __del__. This task changes exactly
  one thing: who decides what start_process() does.

Do not modify any test file. Do not modify UDP4Interface.py, Simulation.py,
LayerStack.py, or LayerProcess.py.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/LinkLayer PiCN/ProgramLibs/ICNForwarder -q --timeout=90
```

**Expect:** identical pass count to the pre-Phase-3 baseline. This is a
refactor with a stated goal of zero behavioural change — any difference,
including a *new* pass, is a bug in the extraction, not a bonus.

---

### Task 3.2 — `UDP4Interface`: add the async contract alongside the sync one

**Goal:** `UDP4Interface` gains `register()`/`send_async()` per ADR-008,
without removing or changing `send()`/`receive()`/`file_descriptor` — both
surfaces coexist on the same class (ADR-008 addendum: no parallel class).
Not wired into `BasicLinkLayer` yet; tested standalone.

> **Naming correction (found during implementation):** ADR-008's own text
> calls the new method `async def send(...)`, reusing `send()`'s name. That
> cannot actually work: Python dispatches on name alone, so a second `send`
> definition on the same class simply shadows the first, silently breaking
> every synchronous caller (`SyncRunStrategy`) rather than raising. The
> implementation (and every reference below) uses `send_async` instead —
> named distinctly, exactly as `register()` already is.

**Files:** `PiCN/Layers/LinkLayer/Interfaces/UDP4Interface.py`, creates
`PiCN/Layers/LinkLayer/Interfaces/test/test_UDP4Interface_async.py`.

**Prompt:**

```
Read PiCN/Layers/LinkLayer/Interfaces/UDP4Interface.py, ADR-008 in full
(including the addendum), and PiCN/Processes/AsyncLayerProcess.py (for
docstring/style consistency) before starting.

In UDP4Interface.py, ADD (do not remove or modify any existing method):

1. async def register(self, queue: asyncio.Queue, interface_id: int) -> None:
   - Must be called from within a running event loop.
   - Uses loop.create_datagram_endpoint(..., sock=self.sock) against the
     ALREADY-BOUND socket created in __init__ -- do not create a new socket
     or rebind.
   - The protocol's datagram_received(data, addr) callback pushes
     (data, addr, interface_id) onto queue. Use queue.put_nowait(...) inside
     a try/except asyncio.QueueFull that DROPS the datagram and logs a
     warning naming the interface_id -- documented as UDP-specific
     (ADR-008 addendum's backpressure decision): UDP already has no delivery
     guarantee, so a drop under backpressure is not the same "hides a bug"
     concern ADR-005 exists to prevent for inter-layer queues. Do NOT use
     await queue.put(...) here -- datagram_received() is a plain callback,
     not a coroutine, and cannot await (this is a deliberate, narrow
     exception to ADR-005 rule 4, not a mistake -- say so in a comment).
   - Store the transport for send() and close() to use.

2. async def send_async(self, data, addr) -> None:
   - Named distinctly from send() -- see the naming correction above.
   - Wraps self._transport.sendto(data, addr). Must only be usable after
     register() has been called; raise a clear RuntimeError otherwise (do
     not silently no-op).
   - Do NOT touch self.sock directly, and do NOT call socket.sendto -- use
     only the transport from register().

Leave send(), receive(), file_descriptor, get_port(), close(),
enable_broadcast(), get_broadcast_address(), __eq__ completely unchanged.

Create PiCN/Layers/LinkLayer/Interfaces/test/test_UDP4Interface_async.py,
a plain pytest module (NOT unittest.TestCase -- see ADR-010's Addendum) with
@pytest.mark.asyncio tests covering:
- register() followed by send(): another real UDP socket receives the data.
- A real UDP packet sent to the interface's port arrives on the queue as
  (data, addr, interface_id) with the interface_id given to register().
- send() before register() raises RuntimeError, does not hang, does not
  silently drop.
- A queue passed with maxsize=1: filling it and then triggering a second
  datagram_received() does not raise out of the protocol callback and does
  not block the event loop -- assert the second item was dropped (queue
  still contains only the first), not silently blocked or crashed.

Do not modify test_UDP4Interface.py -- it characterizes the sync contract
and must keep passing unchanged.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/LinkLayer/Interfaces/test/test_UDP4Interface_async.py PiCN/Layers/LinkLayer/Interfaces/test/test_UDP4Interface.py -v --timeout=30
```

**Expect:** all tests in both files pass. The old file's tests passing
unchanged is the important signal — the sync contract was not disturbed.

---

### Task 3.3 — `AsyncRunStrategy`: the composed engine

**Goal:** `BasicLinkLayer` can now be constructed with
`run_strategy=AsyncRunStrategy()` and behaves identically from the outside
(same queues, same `start_process()`/`stop_process()`) while running a real
asyncio engine internally.

**Files:** `PiCN/Layers/LinkLayer/RunStrategy.py`,
`PiCN/Layers/LinkLayer/BasicLinkLayer.py` (stop_process() only), creates
`PiCN/Layers/LinkLayer/test/test_BasicLinkLayer_async.py`.

**Prompt:**

```
Read ADR-008's addendum and ADR-009's addendum in full before starting --
both govern this task's exact shape. Read PiCN/Processes/AsyncLayerProcess.py
again; you are REUSING it, not reimplementing its pump/cancellation logic.

In RunStrategy.py, add class AsyncRunStrategy(LinkLayerRunStrategy):

- __init__: creates nothing yet (ADR-009: create the executor at start(),
  not at construction).
- start(self, layer: "BasicLinkLayer") -> None: sets
    layer.process = multiprocessing.Process(target=self._entrypoint, args=[layer])
    layer.process.daemon = True
    layer.process.start()
  where _entrypoint(self, layer) runs INSIDE the forked child and does
  asyncio.run(self._async_main(layer)).
- async def _async_main(self, layer) -> None:
  1. Create self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
     -- exactly one, exactly here (ADR-009 addendum).
  2. Create an internal engine: a small concrete subclass of
     PiCN.Processes.AsyncLayerProcess.AsyncLayerProcess whose
     data_from_lower(to_lower, to_higher, data) and
     data_from_higher(to_lower, to_higher, data) reproduce
     BasicLinkLayer.data_from_lower/data_from_higher's EXISTING logic
     (faceidtable lookup, interface selection) but async and awaiting
     interface.send(...) instead of calling it synchronously. Do not
     duplicate the faceidtable/AddressInfo logic by copy-paste without
     understanding it -- read BasicLinkLayer.data_from_lower/data_from_higher
     first and preserve their behaviour exactly, including the existing
     error handling and logging.
  3. Set the engine's queue_from_lower to a fresh asyncio.Queue(maxsize=...)
     (reuse AsyncLayerStack's DEFAULT_QUEUE_SIZE constant, imported, not
     re-defined) and call await interface.register(queue_from_lower,
     interface_id=index) for every interface in layer.interfaces, in order
     (ADR-008: interface_id is the positional index, assigned here, never
     derived any other way).
  4. Set the engine's queue_from_higher to a second fresh asyncio.Queue, and
     start a background bridge task:
       async def _bridge_from_higher():
           loop = asyncio.get_running_loop()
           while True:
               item = await loop.run_in_executor(self._executor, layer._queue_from_higher.get)
               await engine.queue_from_higher.put(item)
     The dispatched function (layer._queue_from_higher.get) takes no
     arguments and returns the item -- pure per ADR-009 rule 5. layer itself
     is never touched inside the executor thread; only its already-existing
     multiprocessing.Queue object's .get is called, which is safe to call
     from any thread.
  5. await engine.run() -- this IS Phase 2's run loop, unmodified, servicing
     both queues concurrently.
  6. On CancelledError (this task itself was cancelled -- see stop() below),
     cancel the bridge task, await it, shut down self._executor with
     wait=True (ADR-009: after tasks stop, never before), then re-raise.

In BasicLinkLayer.py, stop_process() must still work for BOTH strategies
without knowing which one is active: it already just does
self.process.terminate() plus queue closing, which works identically whether
the child process is running the old select() loop or asyncio.run() -- a
terminated process's event loop simply stops existing. Do NOT special-case
stop_process() per strategy; if you find yourself wanting to, that means the
strategies are not sufficiently self-contained -- stop and report instead of
forcing it.

Create PiCN/Layers/LinkLayer/test/test_BasicLinkLayer_async.py (plain pytest,
not unittest.TestCase) covering, using AsyncRunStrategy() explicitly:
- A single node: a real UDP packet sent to the interface's port arrives on
  queue_to_higher with the correct faceid, mirroring
  test_BasicLinkLayer.py::test_receiving_a_packet exactly (same assertions,
  new strategy).
- The reverse: pushing [faceid, data] onto queue_from_higher results in a
  real UDP packet arriving at the expected address, mirroring
  test_sending_a_packet.
- Two BasicLinkLayer instances, both AsyncRunStrategy, exchanging a packet
  each direction, mirroring test_sending_and_receiving_a_packet -- this is
  ADR-008's literal "two nodes exchange real packets" criterion, for the new
  path specifically.

Do not modify test_BasicLinkLayer.py -- it characterizes SyncRunStrategy
(the default) and must keep passing unchanged.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/LinkLayer -v --timeout=90 && grep -rn "recvfrom\|sendto\|select\." PiCN/Layers/LinkLayer/RunStrategy.py
```

**Expect:** all tests pass (old sync tests AND new async ones), and the grep
for blocking socket calls inside the new strategy file returns **empty**.

---

### Task 3.4 — `LegacySyncInterfaceAdapter`

**Goal:** a third party's un-migrated, blocking `BaseInterface`
implementation can still be used under `AsyncRunStrategy`, per ADR-008's
"Migration path for third-party implementations".

**Files:** creates
`PiCN/Layers/LinkLayer/Interfaces/LegacySyncInterfaceAdapter.py` and its test.

**Prompt:**

```
Read ADR-008's "Migration path for third-party implementations" section in
full before starting.

Create PiCN/Layers/LinkLayer/Interfaces/LegacySyncInterfaceAdapter.py:

class LegacySyncInterfaceAdapter(BaseInterface):
    """Wraps an old-style BaseInterface (blocking receive(), synchronous
    send()) so it can be driven by AsyncRunStrategy. DEPRECATED on
    introduction -- this is a migration aid, not a supported long-term path
    (ADR-008)."""

    def __init__(self, wrapped: BaseInterface, executor: concurrent.futures.Executor):
        self._wrapped = wrapped
        self._executor = executor  # INJECTED, never created here -- ADR-009 rule 3/addendum

    async def register(self, queue: asyncio.Queue, interface_id: int) -> None:
        # Starts a background task that loops:
        #   data, addr = await loop.run_in_executor(self._executor, self._wrapped.receive)
        #   await queue.put((data, addr, interface_id))
        # This one CAN use await queue.put() (unlike UDP4Interface's
        # datagram_received) because it runs as a real task, not a
        # callback -- do not use put_nowait here, there is no callback
        # constraint forcing that exception in this file.

    async def send_async(self, data, addr) -> None:
        # Named distinctly from send() -- see Task 3.2's naming correction.
        # await loop.run_in_executor(self._executor, self._wrapped.send, data, addr)

    @property
    def file_descriptor(self):
        raise NotImplementedError(
            "file_descriptor was removed for interfaces driven by "
            "AsyncRunStrategy; see docs/design-adrs/ADR-008-baseinterface-contract.md"
        )

Add a module or class-level DeprecationWarning raised on __init__ (via
warnings.warn), not just a docstring -- ADR-008 says "mark it deprecated on
introduction", which should be enforceable, not just documented.

Create the matching test file covering: send()/receive() round-trip through
the adapter using a simple in-memory fake BaseInterface (do not require a
real socket), and that constructing it emits a DeprecationWarning
(pytest.warns).
```

**Verify:**
```bash
python -m pytest PiCN/Layers/LinkLayer/Interfaces/test/test_LegacySyncInterfaceAdapter.py -v --timeout=30
```

**Expect:** all tests pass, including the `DeprecationWarning` assertion.

---

### Task 3.5 — `SimulationInterface`: the async contract

**Goal:** per ADR-008 rule 7 ("port one interface at a time... UDP4Interface
first, then the simulation interface"), `SimulationInterface` gains the same
`register()`/`send_async()` surface UDP4Interface got in Task 3.2, reusing
the same executor-bridge pattern for its already-multiprocessing-based
`queue_from_bus`. `SimulationBus` itself is unchanged — it still dispatches
via its own `multiprocessing.Queue`-based process; only the per-node
`SimulationInterface` gains an async face.

**Files:** `PiCN/Layers/LinkLayer/Interfaces/Simulation.py`, creates
`PiCN/Layers/LinkLayer/Interfaces/test/test_Simulation_async.py`.

**Prompt:**

```
Read PiCN/Layers/LinkLayer/Interfaces/Simulation.py in full, and Task 3.2's
completed UDP4Interface changes, before starting -- this task mirrors that
one's shape, adapted to SimulationInterface's existing
queue_from_bus/queue_from_linklayer multiprocessing.Queue pair instead of a
socket.

In Simulation.py's SimulationInterface class, ADD (do not remove or modify
send(), receive(), file_descriptor, address(), close()):

1. async def register(self, queue: asyncio.Queue, interface_id: int,
   executor: concurrent.futures.Executor) -> None:
   - executor is INJECTED (ADR-009 rule 3/addendum) -- SimulationInterface
     must not create its own.
   - Starts a background task bridging self.queue_from_bus (a
     multiprocessing.Queue, fed by SimulationBus, exactly as today) into the
     given asyncio.Queue, using the same
     "await loop.run_in_executor(executor, self.queue_from_bus.get)" pattern
     as AsyncRunStrategy's from_higher bridge in Task 3.3. Push
     (packet, addr, interface_id) -- note receive("relay") already unpacks
     this shape; reuse that logic rather than re-deriving it.
   - Store the bridge task so it can be cancelled on close/teardown.

2. async def send_async(self, data, addr) -> None:
   - Named distinctly from send() -- see Task 3.2's naming correction.
   - Wraps self.queue_from_linklayer.put([addr, data]) -- the existing
     send(..., src="relay") body. multiprocessing.Queue.put() on this
     already-unbounded queue does not block in practice, but dispatch it
     through loop.run_in_executor(executor, ...) anyway for correctness
     rather than assuming -- do not call it directly from the event loop.
     This needs the same injected executor as register(); store it from
     register() rather than taking it again here.

Create PiCN/Layers/LinkLayer/Interfaces/test/test_Simulation_async.py (plain
pytest, not unittest.TestCase) covering:
- register() + a manual queue_from_bus.put(...) (simulating what SimulationBus
  would do): the item arrives on the given asyncio.Queue as
  (packet, addr, interface_id).
- send_async(): the data appears on queue_from_linklayer, matching what
  SimulationBus's receive("bus") side expects today.

Do not modify test_Simulation.py -- it characterizes the sync contract via
SimulationBus's existing dispatch loop and must keep passing unchanged. Do
not modify SimulationBus.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/LinkLayer/Interfaces/test/test_Simulation_async.py PiCN/Layers/LinkLayer/Interfaces/test/test_Simulation.py -v --timeout=60
```

**Expect:** all tests in both files pass.

---

### Task 3.6 — Verification sweep and baseline update

**Goal:** confirm every ADR-008/009 rule this phase touches holds, and that
nothing outside the new files changed behaviour.

**Files:** updates `docs/baseline.md` only. No source changes.

**Prompt:**

```
Run each of these and record the output:

  grep -n "self\.sock\." PiCN/Layers/LinkLayer/RunStrategy.py
  grep -n "self\.sock\." PiCN/Layers/LinkLayer/Interfaces/UDP4Interface.py
  grep -n "select\." PiCN/Layers/LinkLayer/Interfaces/Simulation.py
  grep -n "recvfrom\|sendto\|select\." PiCN/Layers/LinkLayer/Interfaces/LegacySyncInterfaceAdapter.py
  grep -rln "def file_descriptor" PiCN/Layers/LinkLayer/ --include='*.py'
  grep -n "\.file_descriptor" PiCN/Layers/LinkLayer/RunStrategy.py
  grep -rn "put_nowait" PiCN/Layers/LinkLayer/ --include='*.py' | grep -v test
  grep -rn "ThreadPoolExecutor\|ProcessPoolExecutor" PiCN/Layers/LinkLayer/ --include='*.py' | grep -v test
  grep -rn "id(self)\|uuid" PiCN/Layers/LinkLayer/ --include='*.py' | grep -v test

NOTE: a blanket "recvfrom|sendto|select\." grep across UDP4Interface.py or
Simulation.py as a WHOLE FILE is the wrong check and will show false
positives -- those files still legitimately contain their ORIGINAL sync
send()/receive()/SimulationBus._run() bodies, unchanged, for SyncRunStrategy's
sake. Check self.sock./select. usage specifically, and confirm BY READING,
not just grepping, that every hit falls inside a method that predates this
phase (send(), receive(), get_port(), close(), enable_broadcast() for
UDP4Interface; SimulationBus._run() for Simulation.py) rather than inside
register()/send_async().

Expected results, per ADR-008's addendum and ADR-009's addendum:
- First (RunStrategy.py): empty -- it never touches a raw socket at all.
- Second (UDP4Interface.py): hits only inside __init__/send()/receive()/
  get_port()/close()/enable_broadcast() -- the original, unchanged sync
  surface. None inside register()/send_async().
- Third (Simulation.py): hits only inside SimulationBus._run() -- confirm by
  line number that they fall after "class SimulationBus", not inside
  SimulationInterface.
- Fourth (LegacySyncInterfaceAdapter.py): empty -- it delegates to the
  wrapped interface's own send()/receive() rather than touching a socket
  directly.
- Fifth: exactly FOUR files define file_descriptor -- BaseInterface.py (the
  raising default), UDP4Interface.py and Simulation.py (real, unchanged
  overrides, for SyncRunStrategy's sake), and LegacySyncInterfaceAdapter.py
  (also raising -- nothing driven by AsyncRunStrategy, native or adapted,
  supports this). This is EXPECTED, not a violation; see the ADR-008
  addendum for why the "exactly one hit" language in the original ADR text
  does not apply once SyncRunStrategy is kept alive.
- Sixth: empty (the new async run strategy never touches file_descriptor).
- Seventh: exactly one real hit, inside UDP4Interface.py's
  datagram_received callback, with a comment explaining why (the ADR-005
  exception for a non-coroutine callback) -- a second match that is only a
  comment MENTIONING put_nowait in prose (e.g. in
  LegacySyncInterfaceAdapter.py, explaining why it does NOT need that
  exception) is not a violation.
- Eighth: hits only inside RunStrategy.py (AsyncRunStrategy) -- exactly one
  executor construction, matching ADR-009's addendum. Any hit inside
  UDP4Interface.py, Simulation.py, or LegacySyncInterfaceAdapter.py is a
  violation -- those receive an executor by injection, they must never
  create one.
- Ninth: empty -- interface_id always comes from register()'s parameter,
  never invented.

Then run the full suite:
  python -m pytest -v --timeout=90 -p no:cacheprovider > /tmp/phase3-run.txt 2>&1
  tail -5 /tmp/phase3-run.txt

Compare against the "After Phase 2" counts in docs/baseline.md. Every
previously-passing test must still pass. New tests from Tasks 3.1-3.5 add
passes; nothing else should change. Any other change is a regression --
report it, do not fix it in this task.

Add a new section to docs/baseline.md titled "After Phase 3" with the six
grep results, the new counts and delta, and explicit confirmation that
SyncRunStrategy-driven BasicLinkLayer (i.e. every existing ProgramLib) is
byte-for-byte unaffected.
```

**Verify:**
```bash
grep -A15 "After Phase 3" docs/baseline.md
```

**Expect:** the new section, all six grep checks matching their expected
shape above, and no regressions versus "After Phase 2".

---

### Task 3.7 — Commit Phase 3

**Goal:** land the I/O boundary work as a single, reviewable commit.

**Files:** `PiCN/Layers/LinkLayer/RunStrategy.py`,
`PiCN/Layers/LinkLayer/BasicLinkLayer.py`,
`PiCN/Layers/LinkLayer/Interfaces/UDP4Interface.py`,
`PiCN/Layers/LinkLayer/Interfaces/Simulation.py`,
`PiCN/Layers/LinkLayer/Interfaces/LegacySyncInterfaceAdapter.py`, their new
test files, `docs/baseline.md`.

**Prompt:**

```
Confirm git status --short shows ONLY the files listed above (plus the usual
ignored artifacts). If anything else changed, stop and report it.

Stage exactly those files and commit with this message:

  Phase 3: asyncio I/O boundary via an injected LinkLayerRunStrategy

  Introduces AsyncRunStrategy alongside the untouched SyncRunStrategy
  (ADR-008's 2026-08-04 addendum): every ProgramLib gets SyncRunStrategy by
  default and is byte-for-byte unaffected; a ProgramLib opts into
  AsyncRunStrategy explicitly, one at a time, ahead of Phase 5's node
  assembly.

  UDP4Interface and SimulationInterface gain register()/send_async()
  alongside their existing sync methods -- one class, two coexisting
  surfaces, not a parallel hierarchy. AsyncRunStrategy composes Phase 2's
  AsyncLayerProcess as BasicLinkLayer's internal engine, bridging the still-
  multiprocessing queue_from_higher via one narrowly-scoped executor
  (ADR-009's addendum), deleted rather than migrated once Phase 5 wires a
  real AsyncLayerStack in.

  LegacySyncInterfaceAdapter lets a third-party, un-migrated BaseInterface
  implementation keep working under AsyncRunStrategy (deprecated on
  introduction).

  Verified via new standalone tests plus a full-suite regression run
  recorded in docs/baseline.md ("After Phase 3") -- every existing test,
  including every ProgramLib's, is unaffected.

Then show: git status --short && git log --oneline -1
```

**Verify:**
```bash
git status --short && git log --oneline -1
```

**Expect:** a clean working tree apart from ignored files, and the new commit.

---

## Phase 3 exit criteria

Do not start Phase 4 until **all** are true:

- [x] `SyncRunStrategy` reproduces today's `BasicLinkLayer` behaviour exactly
      — every existing `LinkLayer`/`ProgramLibs` test still passes, unchanged
- [x] `AsyncRunStrategy` exists, is opt-in, and two `BasicLinkLayer` instances
      running it exchange real UDP packets in both directions
- [x] `UDP4Interface` and `SimulationInterface` each carry both method
      surfaces on one class — no parallel interface hierarchy
- [x] No blocking `recvfrom`/`sendto`/`select.*` call exists anywhere in
      `AsyncRunStrategy` or the interfaces' new async methods
- [x] `file_descriptor` is untouched by the new async path (still used only
      by `SyncRunStrategy` and its unchanged call sites)
- [x] Exactly one executor is created, inside `AsyncRunStrategy`, injected
      into everything else that needs one
- [x] `LegacySyncInterfaceAdapter` exists, is marked deprecated, and is tested
- [x] `docs/baseline.md` has an "After Phase 3" section showing the full
      suite unaffected outside the new test files

---

# Phase 4 — Remaining layers via extract-core

**Purpose:** port every remaining layer onto the async model **without**
breaking ProgramLibs still on `LayerStack`. Pattern (ADR-003's 2026-08-04
addendum): extract handler logic into a non-process `*Core` module that
returns `List[Outbound]`; keep the existing `LayerProcess` subclass as a
thin sync wrapper; add a thin `AsyncLayerProcess` subclass. One layer per
task. Do not wire any ProgramLib to an async wrapper in this phase.

**Commit policy:** one commit for this planning expansion; then one commit
per layer (Tasks 4.0–4.8); final baseline commit (Task 4.9).

> **Read first:** [ADR-003](design-adrs/ADR-003-handler-lifecycle.md)
> (including the 2026-08-04 extract-core addendum),
> [ADR-009](design-adrs/ADR-009-cpu-bound-work.md) (including its 2026-08-04
> addendum — `AsyncRunStrategy`'s temporary executor stays until Phase 5;
> `AsyncLayerStack` owns the stack executor for async layers), and
> [ADR-010](design-adrs/ADR-010-async-test-strategy.md) (plain pytest classes
> for async tests, capital `Test` prefix).

### Task 4.0 — `AsyncLayerStack` owns the executor

**Goal:** `AsyncLayerStack` creates exactly one `ThreadPoolExecutor` at
`start_all()`, injects it onto layers that expose `set_executor` /
`executor`, and shuts it down with `wait=True` only after layer tasks have
stopped (ADR-009).

**Files:** `PiCN/LayerStack/AsyncLayerStack.py`, creates
`PiCN/LayerStack/test/test_AsyncLayerStack_executor.py`.

**Prompt:**

```
Read ADR-009 in full (including the 2026-08-04 addendum) and
AsyncLayerStack.py before starting.

In AsyncLayerStack:
- Add optional constructor arg executor_workers: int = 4 (named constant
  or default is fine; do not create the pool in __init__).
- In start_all(): create self._executor = ThreadPoolExecutor(
  max_workers=executor_workers) BEFORE starting layer tasks. For each
  layer in self.layers, if hasattr(layer, "executor") as a writable
  attribute or a set_executor method, inject self._executor. Do not
  require every layer to accept an executor -- PacketEncoding will not
  need one.
- In stop_all(): after awaiting cancelled layer tasks (existing logic),
  call self._executor.shutdown(wait=True) if it exists. Never shut the
  executor down before layers stop.
- Expose @property executor for tests.

Create test_AsyncLayerStack_executor.py (plain pytest, Test prefix,
@pytest.mark.asyncio):
- start_all creates an executor; stop_all shuts it down (subsequent
  submit raises RuntimeError or the pool is marked shutdown).
- A stub AsyncLayerProcess with an .executor attribute receives the
  injected executor on start_all.
- Ordering: a layer whose stop is slow still completes before
  executor.shutdown (use a short sleep in stop path or a done-callback
  probe -- do not flake).

Do not modify LayerStack.py (multiprocessing). Do not remove
AsyncRunStrategy's executor (Phase 3 temporary exception).
```

**Verify:**
```bash
python -m pytest PiCN/LayerStack -q --timeout=60
```

**Expect:** all LayerStack tests pass, including the new executor tests.

---

### Task 4.1 — PacketEncoding: characterization, core, async wrapper

**Goal:** establish the extract-core pattern on the simplest layer.

**Files:** creates `PiCN/Processes/Outbound.py`,
`PiCN/Layers/PacketEncodingLayer/PacketEncodingCore.py`,
`PiCN/Layers/PacketEncodingLayer/AsyncBasicPacketEncodingLayer.py`,
`PiCN/Layers/PacketEncodingLayer/test/test_characterization.py`,
`PiCN/Layers/PacketEncodingLayer/test/test_AsyncBasicPacketEncodingLayer.py`;
modifies `BasicPacketEncodingLayer.py`, `__init__.py`.

**Prompt:**

```
Read BasicPacketEncodingLayer.py and ADR-003's extract-core addendum
in full. Read ICNLayer's test_characterization.py for the sync
characterization style.

Step A -- characterization BEFORE extract:
Create test/test_characterization.py locking current BasicPacketEncodingLayer
behaviour via direct data_from_lower/data_from_higher calls with queue.Queue
(no multiprocessing). Cover: valid encode path to_lower, valid decode path
to_higher, malformed data (wrong length / non-int face id) drops with empty
queues. Use SimpleStringEncoder. Run and confirm green BEFORE any extract.

Step B -- shared Outbound:
Create PiCN/Processes/Outbound.py with the frozen dataclass from ADR-003's
addendum. Export from PiCN/Processes/__init__.py if that module already
re-exports public types.

Step C -- PacketEncodingCore:
Move check_data/encode/decode and the handler bodies into
PacketEncodingCore. handle_from_higher / handle_from_lower return
List[Outbound]. Core takes encoder + logger (or a thin logger-like
object). Core must not import or touch Queue.

Step D -- slim BasicPacketEncodingLayer:
Keep the same public API (__init__, encoder property, data_from_*,
encode, decode, check_data can delegate to core). data_from_* apply
Outbound via to_lower.put / to_higher.put. Existing
test_BasicPacketEncodingLayer.py must pass unchanged.

Step E -- AsyncBasicPacketEncodingLayer(AsyncLayerProcess):
Same core; async def data_from_* apply Outbound with await put.
Export from package __init__.py.

Step F -- async tests:
test_AsyncBasicPacketEncodingLayer.py -- plain pytest Test* class,
@pytest.mark.asyncio, mirror the characterization cases with
asyncio.Queue.

Do not modify ProgramLibs. Do not delete encode/decode from the sync
class's public surface if tests call them.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/PacketEncodingLayer -q --timeout=30
```

**Expect:** characterization + sync + async + encoder tests all pass.

---

### Task 4.2 — ICNLayer extract-core + async wrapper

**Goal:** port ICN forwarding logic into a core; async wrapper; ageing via
asyncio on the async path only.

**Files:** creates `ICNLayerCore.py`, `AsyncBasicICNLayer.py`,
`test/test_AsyncBasicICNLayer.py`; modifies `BasicICNLayer.py`.

**Prompt:**

```
Read BasicICNLayer.py in full and test/test_characterization.py.
Extract handle_* / data_from_* logic into ICNLayerCore returning
List[Outbound]. Preserve observed behaviour including existing log
messages and error paths (ADR-001). Slim BasicICNLayer keeps ageing()
with threading.Timer unchanged. AsyncBasicICNLayer: async handlers;
start() also starts an ageing task that periodically runs the same
ageing logic the sync path uses (emit Outbounds / put to queues),
cancelled in stop(). Existing characterization and test_BasicICNLayer
must pass. New async tests cover at least the characterization scenarios
awaited against AsyncBasicICNLayer. Do not change CS/FIB/PIT classes.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/ICNLayer -q --timeout=90
```

**Expect:** all ICNLayer tests pass.

---

### Task 4.3 — ChunkLayer extract-core + async wrapper

**Goal:** same pattern; async path uses plain dict/list instead of
`multiprocessing.Manager` proxies where the core owns request/chunk tables.

**Files:** creates `ChunkLayerCore.py`, `AsyncBasicChunkLayer.py`, tests;
modifies `BasicChunkLayer.py`.

**Prompt:**

```
Read BasicChunkLayer.py. Extract core returning List[Outbound]. Sync
wrapper may keep Manager-backed structures if required for MP sharing.
Async wrapper constructs plain dict/list storage for the core. Add
characterization if none exists (direct handler calls) before extract.
Existing chunk tests must pass on the sync class. Async tests cover
round-trip chunking interest/content paths with asyncio.Queue.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/ChunkLayer -q --timeout=60
```

**Expect:** all ChunkLayer tests pass.

---

### Task 4.4 — RepositoryLayer extract-core + async wrapper

**Goal:** thin core; audit repository `get_content` for blocking I/O;
use injected executor via `run_in_executor` only if measured >~10ms
(ADR-009 rule 1) — otherwise call on the loop and document the
measurement in the commit message.

**Files:** creates `RepositoryLayerCore.py`, `AsyncBasicRepositoryLayer.py`,
tests; modifies `BasicRepositoryLayer.py`.

**Prompt:**

```
Read BasicRepositoryLayer.py and SimpleFileSystemRepository. Extract
core. Async wrapper accepts optional executor=None; if executor is set
and the repo is file-backed, dispatch get_content through
run_in_executor with a pure function (path/args in, content out -- no
layer self). If leaving on the loop, add a comment with the measurement.
Existing tests pass. Add async tests with an in-memory repository.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/RepositoryLayer -q --timeout=60
```

**Expect:** all RepositoryLayer tests pass.

---

### Task 4.5 — TimeoutPreventionLayer extract-core + async wrapper

**Goal:** extract core; sync keeps `threading.Timer` ageing; async uses
asyncio task.

**Files:** creates `TimeoutPreventionCore.py`,
`AsyncBasicTimeoutPreventionLayer.py`, tests; modifies
`BasicTimeoutPreventionLayer.py`.

**Prompt:**

```
Read BasicTimeoutPreventionLayer.py. Extract core returning
List[Outbound]. Preserve KEEPALIVE/R2C behaviour. Async ageing task
mirrors sync interval. Existing tests pass; add async tests for at
least one keepalive path.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/TimeoutPreventionLayer -q --timeout=60
```

**Expect:** all TimeoutPreventionLayer tests pass.

---

### Task 4.6 — NFNLayer extract-core + async wrapper

**Goal:** extract core; CPU-bound `executor.execute` goes through the
injected stack executor (pure wrt layer state -- ADR-009).

**Files:** creates `NFNLayerCore.py`, `AsyncBasicNFNLayer.py`, tests;
modifies `BasicNFNLayer.py`.

**Prompt:**

```
Read BasicNFNLayer.py. Extract core. Async wrapper requires executor
injection for named-function execution: run_in_executor(executor, pure_fn,
...). Do not pass self into the executor. Sync path unchanged behaviour.
Existing NFNLayer tests pass; add async tests covering a simple compute
path with a ThreadPoolExecutor injected manually (AsyncLayerStack wiring
is Phase 5).
```

**Verify:**
```bash
python -m pytest PiCN/Layers/NFNLayer -q --timeout=120
```

**Expect:** all NFNLayer tests pass (same known x86 native failures on
macOS if they appear -- do not "fix" them here).

---

### Task 4.7 — ThunkLayer extract-core + async wrapper

**Goal:** extract stateful thunk logic into a core; async wrapper.

**Files:** creates `ThunkLayerCore.py`, `AsyncBasicThunkLayer.py`, tests;
modifies thunk layer module.

**Prompt:**

```
Read BasicThunkLayer.py (or equivalent). Extract core returning
List[Outbound]. No timer changes unless the layer has one. Existing
tests pass; add async characterization-style tests.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/ThunkLayer -q --timeout=90
```

**Expect:** all ThunkLayer tests pass.

---

### Task 4.8 — RoutingLayer + AutoconfigLayer

**Goal:** extract-core for both packages (two commits if needed, but one
task checklist); timer-driven work becomes asyncio on async wrappers
only. Sync `start_process`/`stop_process` timer behaviour preserved.

**Files:** Routing and Autoconfig layer modules + async wrappers + tests.

**Prompt:**

```
Port RoutingLayer then AutoconfigLayer using the established pattern.
Do not batch their commits with unrelated layers. Preserve broadcast /
RIB ageing behaviour. Existing tests pass; add minimal async tests per
package.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/RoutingLayer PiCN/Layers/AutoconfigLayer -q --timeout=120
```

**Expect:** all tests in both packages pass.

---

### Task 4.9 — Verification sweep and baseline update

**Goal:** confirm extract-core rules hold; full suite vs Phase 3 baseline.

**Files:** updates `docs/baseline.md` only (plus checklist ticks in
agent-tasks if you mark exit criteria).

**Prompt:**

```
Run:
  grep -rn "to_lower\.put\|to_higher\.put" PiCN/Layers/ --include='*Core.py'
  grep -rn "ThreadPoolExecutor\|ProcessPoolExecutor" PiCN/LayerStack/ PiCN/Layers/ --include='*.py' | grep -v test
  grep -rn "async def data_from_lower" PiCN/Layers/ --include='*.py' | grep -v test

Expect: no puts in *Core.py; ThreadPoolExecutor only in AsyncLayerStack
(and AsyncRunStrategy's Phase-3 temporary one in LinkLayer/RunStrategy.py);
async def data_from_lower present for each migrated async wrapper.

Full suite:
  python -m pytest -v --timeout=90 -p no:cacheprovider > /tmp/phase4-run.txt 2>&1
  tail -5 /tmp/phase4-run.txt

Compare to "After Phase 3". No regressions outside new tests. Add
"After Phase 4" to docs/baseline.md.
```

**Verify:**
```bash
grep -A20 "After Phase 4" docs/baseline.md
```

**Expect:** section present; no regressions.

---

## Phase 4 exit criteria

Do not start Phase 5 until **all** are true:

- [x] `AsyncLayerStack` owns exactly one executor; layers never create one
- [x] Every Phase-4 layer has `*Core` + sync wrapper + `Async*` wrapper
- [x] No `*Core.py` touches queues
- [x] Existing sync/ProgramLib tests still pass
- [x] Each async wrapper has tests
- [x] `docs/baseline.md` has "After Phase 4"

---

# Phase 5 — Node assembly via shared builders

**Purpose:** wire `ProgramLibs`, `Mgmt`, `starter/` executables, and one
simulation onto `AsyncLayerStack` + async layer wrappers, without a
parallel `AsyncICNForwarder` hierarchy.

**Decisions (locked 2026-08-04):**

1. **Shared builders + stack choice** — one builder per node type;
   `runtime="sync"|"async"` (default `"sync"`).
2. **Async runtime uses plain in-process CS/FIB/PIT/FaceIDTable** — no
   `PiCNSyncDataStructFactory` / Manager on the async path.
3. **`AsyncMgmt`** — asyncio TCP server in the same event loop (ADR-006
   addendum).
4. **Include `NFNForwarderData`** (default yes — used by data-offloading
   simulations; drop only if explicitly requested).
5. **Coverage:** do not block Phase 5 on per-module ≥80%; aggregate ≥80%
   from Phase 4 is enough. Coverage fill is Phase 8.

**Simulation exit gate (default):**
[`PiCN/Simulations/SimulationsTutorial.py`](../PiCN/Simulations/SimulationsTutorial.py)
runnable under async ProgramLibs (or a documented minimal scenario if that
file proves too heavy — record the choice in the baseline).

**Commit policy:** one commit for this planning expansion; then one commit
per task group (builders/link, Mgmt, each ProgramLib family, starters,
simulation, baseline).

> **Read first:** [ADR-004](design-adrs/ADR-004-async-layerstack.md)
> (2026-08-04 shared-builder addendum),
> [ADR-006](design-adrs/ADR-006-shutdown-cancellation.md) (AsyncMgmt
> addendum), [ADR-009](design-adrs/ADR-009-cpu-bound-work.md),
> [ADR-008](design-adrs/ADR-008-baseinterface-contract.md) (link/interfaces).

### Task 5.0 — Promote `AsyncBasicLinkLayer`; shared runtime helpers

**Goal:** public `AsyncBasicLinkLayer` usable inside `AsyncLayerStack`
(no forked process, no Phase-3 bridge executor). Shared helpers for
`runtime` selection and plain vs Manager data structs.

**Files:** creates/moves `PiCN/Layers/LinkLayer/AsyncBasicLinkLayer.py`
(from `RunStrategy._LinkLayerEngine`); creates
`PiCN/ProgramLibs/runtime.py` (or `builders/common.py`) with
`Runtime` enum and `make_icn_tables(runtime)` helpers; tests.

**Prompt:**

```
Read RunStrategy.py (_LinkLayerEngine, AsyncRunStrategy) and ADR-004's
2026-08-04 addendum.

1. Promote _LinkLayerEngine to AsyncBasicLinkLayer(AsyncLayerProcess) in
   its own module. register() every interface with the stack's
   queue_from_lower; interface_id = enumerate index. Accept optional
   executor for SimulationInterface.register(..., executor=). Wire
   send_async on outbound. Export from LinkLayer __init__.

2. AsyncRunStrategy may keep using the engine internally for backwards
   compatibility OR delegate to AsyncBasicLinkLayer -- do not break
   test_BasicLinkLayer_async.py.

3. Add ProgramLibs/runtime.py:
   class Runtime(str, Enum): SYNC = "sync"; ASYNC = "async"
   def make_forwarding_tables(runtime, ...) -> named tuple of cs,fib,pit,
   faceidtable[,rib]: SYNC uses PiCNSyncDataStructFactory; ASYNC
   constructs ContentStoreMemoryExact etc. directly (plain objects).

4. Tests: AsyncBasicLinkLayer packet exchange with AsyncLayerStack (two
   nodes or UDP loopback) without multiprocessing.Process. Existing
   LinkLayer async tests still pass.

Do not modify ProgramLibs node classes yet.
```

**Verify:**
```bash
python -m pytest PiCN/Layers/LinkLayer PiCN/LayerStack -q --timeout=90
```

**Expect:** all pass, including new AsyncBasicLinkLayer + stack tests.

---

### Task 5.1 — `AsyncMgmt`

**Goal:** asyncio TCP management server with the same HTTP semantics as
sync `Mgmt`, no `terminate()` / `sleep`.

**Files:** creates `PiCN/Mgmt/AsyncMgmt.py` +
`PiCN/Mgmt/test/test_AsyncMgmt.py`; sync `Mgmt.py` unchanged.

**Prompt:**

```
Read Mgmt.py in full and ADR-006's AsyncMgmt addendum.

Implement AsyncMgmt with:
- __init__(cs, fib, pit, linklayer, port, shutdown=None, ...)
- async def start() -- asyncio.start_server on 127.0.0.1:port; store
  server + serve task
- async def stop(timeout=SHUTDOWN_TIMEOUT) -- close server, cancel serve
  task, await bounded (asyncio.wait, not wait_for alone -- see ADR-006)
- Request handling: reuse/adapt sync parsing so FIB/CS ops and
  /shutdown callback behaviour match observed Mgmt (characterize with
  tests against sync first if unsure)
- shutdown callback for async is an async callable or schedules
  stop_forwarder; do not time.sleep(2)

Tests (plain pytest + asyncio): start, add FIB entry via HTTP client
(asyncio open_connection), shutdown path cancels cleanly. Do not break
test_Mgmt.py.
```

**Verify:**
```bash
python -m pytest PiCN/Mgmt -q --timeout=60
```

**Expect:** sync + async Mgmt tests pass.

---

### Task 5.2 — ICNForwarder shared builder

**Goal:** `ICNForwarder(..., runtime=Runtime.SYNC|ASYNC)` builds the
appropriate stack; default SYNC unchanged.

**Files:** `ICNForwarder.py`, creates builder helper if needed,
`test_ICNForwarder_async.py`.

**Prompt:**

```
Refactor ICNForwarder construction through a shared builder. Default
runtime=SYNC must keep existing tests green without edits where
possible.

ASYNC path:
- plain tables via make_forwarding_tables(ASYNC)
- AsyncBasicICNLayer, AsyncBasicPacketEncodingLayer, AsyncBasicLinkLayer
  (plus async autoconfig/routing wrappers if those flags are set)
- AsyncLayerStack; inject executor into layers that need it
- AsyncMgmt
- Prefer explicit async start_forwarder_async / stop_forwarder_async
  (and never overload sync start_forwarder() as a coroutine -- sync
  callers must keep calling start_forwarder() without await).

Do NOT call icnlayer.ageing() on the async path.
Add async integration test: start, mgmt or UDP interest/content path,
stop. Sync test_ICNForwarder.py must pass unchanged.
```

**Verify:**
```bash
python -m pytest PiCN/ProgramLibs/ICNForwarder -q --timeout=90
```

**Expect:** all pass.

---

### Task 5.3 — Fetch shared builder

**Goal:** same pattern for `Fetch` (picn-fetch exit criterion).

**Files:** `Fetch.py`, async tests.

**Prompt:**

```
Apply Task 5.2's pattern to Fetch (runtime= + start_fetch_async /
stop_fetch_async). CRITICAL: sync Fetch calls start_all() inside
__init__ today -- the ASYNC path must NOT start the stack from
__init__ (there is no running event loop). Defer start to
await start_fetch_async(). Keep sync __init__ behaviour unchanged.
Async path: AsyncLayerStack + async layers + no Mgmt. Ensure
fetch_data / interest paths work under asyncio.run. Sync tests
unchanged.
```

**Verify:**
```bash
python -m pytest PiCN/ProgramLibs/Fetch -q --timeout=120
```

**Expect:** pass except known native-code FetchNFN failures on macOS.

---

### Task 5.4 — NFNForwarder (+ Thunk) shared builder

**Goal:** NFN forwarder async runtime; ageing only on sync path.

**Files:** `NFNForwarder.py`, async tests.

**Prompt:**

```
Shared builder for NFNForwarder. Async: AsyncBasicNFNLayer with
executor from AsyncLayerStack, AsyncBasicChunkLayer,
AsyncBasicTimeoutPreventionLayer, optional AsyncBasicThunkLayer,
AsyncBasicICNLayer, etc. Use start_forwarder_async /
stop_forwarder_async. Do not call ageing() on async wrappers.
Async tests: simple compute interest if feasible; otherwise stack
start/stop + one packet path. Sync tests unchanged (known x86
failures ok).
```

**Verify:**
```bash
python -m pytest PiCN/ProgramLibs/NFNForwarder -q --timeout=120
```

**Expect:** sync suite as before; new async tests green.

---

### Task 5.5 — ICNDataRepository, ICNPushRepository, NFNForwarderData

**Goal:** remaining ProgramLibs on the shared-builder pattern
(including NFNForwarderData).

**Files:** the three ProgramLib modules + async smoke tests.

**Prompt:**

```
Port ICNDataRepository, ICNPushRepository, and NFNForwarderData using
the same runtime= switch and *_async start/stop names. One commit is
fine for this task if the diffs stay reviewable; split if large. Sync
tests must pass.
```

**Verify:**
```bash
python -m pytest PiCN/ProgramLibs/ICNDataRepository PiCN/ProgramLibs/ICNPushRepository PiCN/ProgramLibs/NFNForwarder -q --timeout=120
```

**Expect:** pass (same known failures only).

---

### Task 5.6 — Executables / starter SIGINT

**Goal:** async-capable entry points run `asyncio.run` when asked;
SIGINT cancels cleanly (ADR-006).

**Files:** `PiCN/Executable/*.py`, possibly `starter/` wrappers.

**Prompt:**

```
Add a --runtime async|sync flag (default sync) to picn-relay,
picn-fetch, and picn-nfn executables (and repo/pushrepo if
straightforward). Async main (single node -- one asyncio.run):
  try: await node.start_*_async(); await wait_forever_or_event()
  finally: await node.stop_*_async()
Install SIGINT/SIGTERM handlers that trigger stop. Sync path
unchanged. Smoke-test by importing and running start/stop in
pytest with runtime=async where practical -- do not require
manual CLI in CI.
```

**Verify:**
```bash
python -m pytest PiCN/ProgramLibs PiCN/Executable -q --timeout=120 2>/dev/null || python -m pytest PiCN/ProgramLibs -q --timeout=120
```

**Expect:** no new failures.

---

### Task 5.7 — One simulation under async ProgramLibs

**Goal:** Phase 5 exit criterion — at least one simulation scenario runs
with async runtime.

**Files:** adapt `SimulationsTutorial.py` (or document alternate) to
accept runtime=async; minimal test or scripted verify.

**Prompt:**

```
Wire SimulationsTutorial (default gate) to construct forwarders with
runtime=async. CRITICAL: multi-node simulations must use ONE shared
event loop -- start every async node as a task on that loop; do NOT
call asyncio.run() per forwarder. SimulationBus may remain sync
(Phase 3 SimulationInterface.register + stack executor). Verify the
tutorial's basic exchange completes. Record which scenario was used
in docs/baseline.md.
```

**Verify:**
```bash
python -m pytest PiCN/Simulations -q --timeout=120 2>/dev/null; .venv/bin/python -c "print('manual sim verify documented in baseline')"
```

**Expect:** scenario completes; documented in baseline.

---

### Task 5.8 — Verification sweep + baseline

**Goal:** ADR greps + full suite vs Phase 4 baseline; "After Phase 5".

**Prompt:**

```
grep -rn "AsyncICNForwarder\|AsyncNFNForwarder" PiCN/ProgramLibs/
grep -rn "create_manager" PiCN/ProgramLibs/ --include='*.py' | grep -v test
# async builders must not hit create_manager -- inspect by reading

Full suite; compare to After Phase 4. Add After Phase 5 to
docs/baseline.md. Tick exit criteria.
```

**Verify:**
```bash
grep -A15 "After Phase 5" docs/baseline.md
```

**Expect:** section present; no regressions outside new tests.

---

## Phase 5 exit criteria

Do not start Phase 6 until **all** are true:

- [ ] Shared builders exist; default runtime remains sync
- [ ] Async runtime uses plain in-process tables (no Manager)
- [ ] `AsyncMgmt` + `AsyncBasicLinkLayer` in the node event loop
- [ ] `picn-relay`, `picn-fetch`, NFN forwarder work e2e under async
- [ ] `NFNForwarderData` included (or explicitly deferred in baseline)
- [ ] At least one simulation runs under async ProgramLibs
- [ ] `docs/baseline.md` has "After Phase 5"; no ProgramLib regressions
      on the sync default path

---

# Phases 6–7 — expand before use

| Phase | Theme | Governing ADRs | Expand when |
|---|---|---|---|
| 6 | Delete the multiprocessing scaffolding | 002, 004 | Phase 5 exits |
| 7 | Test infrastructure and CI | 010 | Phase 6 exits |
| 8 | Coverage completion (runs continuously) | 010 | Any time after 7 |

### Rules that carry forward

- Phase 6 deletes code. Only delete what Phases 2–5 made unreachable —
  verify with `grep`, not assumption.
- CI (Phase 7) should run the fast checks on every push, and slower
  full-stack tests on pull requests.

---

## Reference: task template

```
### Task N.M — <short title>

**Goal:** <one sentence>

**Files:** <exact paths, or "creates X">

**Prompt:**
<verbatim block, imperative, with explicit do-nots>

**Verify:**
<single shell command>

**Expect:** <exact success condition>
```
