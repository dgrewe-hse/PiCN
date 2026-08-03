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

# Phases 2–7 — expand before use

The remaining phases are specified in [`modernization.md`](modernization.md) but
are **not yet broken down to prompt level**. Expand each into tasks using the
same format before handing to a small model:

| Phase | Theme | Governing ADRs | Expand when |
|---|---|---|---|
| 2 | Async foundations — the single run loop, lifecycle, async `LayerStack` | 003, 004, 005, 006, 007, 010 | Phase 1 exits |
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
