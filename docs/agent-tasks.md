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
| 3 | ADR-008 |
| 4 | ADR-009 |

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
     ADR-004 rule 1:

       async def run(self) -> None:
           async def _pump_lower() -> None:
               while True:
                   data = await self.queue_from_lower.get()
                   await self.data_from_lower(self.queue_to_lower, self.queue_to_higher, data)

           async def _pump_higher() -> None:
               while True:
                   data = await self.queue_from_higher.get()
                   await self.data_from_higher(self.queue_to_lower, self.queue_to_higher, data)

           async with asyncio.TaskGroup() as tg:
               if self.queue_from_lower is not None:
                   tg.create_task(_pump_lower())
               if self.queue_from_higher is not None:
                   tg.create_task(_pump_higher())

     Note: nesting these two pumps in their own TaskGroup means a handler
     exception on one side cancels the other side of the SAME layer and
     propagates out of run() — this is what task 2.3/2.4's stack-level
     supervision then observes.
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

Create PiCN/Processes/test/test_AsyncLayerProcess.py, matching the style of
test_LayerProcess.py but using pytest-asyncio (@pytest.mark.asyncio on every
async test, per ADR-010). Define one minimal concrete subclass of
AsyncLayerProcess for testing (e.g. one that appends received data to a list,
or echoes it onto the opposite queue). Cover:
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

Create PiCN/LayerStack/test/test_AsyncLayerStack.py, matching the style of
test_LayerStack.py, using pytest-asyncio for anything that touches a running
loop. Reuse the concrete AsyncLayerProcess subclass pattern from Task 2.2 (a
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

# Phases 3–7 — expand before use

The remaining phases are specified in [`modernization.md`](modernization.md) but
are **not yet broken down to prompt level**. Expand each into tasks using the
same format before handing to a small model:

| Phase | Theme | Governing ADRs | Expand when |
|---|---|---|---|
| 3 | I/O boundary — `BaseInterface` contract, `UDP4Interface`, `BasicLinkLayer` | 008 | Phase 2 exits |
| 4 | Remaining layers, simplest first | 009 | Phase 3 exits |
| 5 | Node assembly — `ProgramLibs`, `Mgmt`, `starter/`, simulations | 006 | Phase 4 exits |
| 6 | Delete the multiprocessing scaffolding | 002, 004 | Phase 5 exits |
| 7 | Test infrastructure and CI | 010 | Phase 6 exits |
| 8 | Coverage completion (runs continuously) | 010 | Any time after 7 |

The design decisions for these phases are **already made** — see
[`design-adrs/`](design-adrs/README.md). What is missing is only the breakdown
into atomic tasks, which depends on what the preceding phase actually produced.

**Why not written now:** each phase's tasks depend on what the previous phase
actually produced. Writing them in advance would specify against a codebase that
does not exist yet, and small models follow precise instructions well but
recover from wrong ones badly.

### Rules that carry forward

- One layer per task in Phases 3 and 4. Never batch layers.
- Every task that changes behaviour must end with a baseline comparison, as in
  Task 1.8.
- Phase 6 deletes code. Only delete what Phases 2–5 made unreachable — verify
  with `grep`, not assumption.
- CI (Phase 7) should run the fast checks on every push, and slower full-stack
  tests on pull requests.

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
