# AGENTS.md — Working conventions for AI coding agents

Instructions for AI coding agents (and humans) contributing to this fork of
[PiCN](https://github.com/cn-uofbasel/PiCN). Read this before making changes.

## What this project is

PiCN is a modular Python implementation of Information-Centric Networking (ICN)
and Named Function Networking (NFN), originally from the Computer Networks Group
at the University of Basel. It provides a layered network stack — link layer,
packet encoding, ICN forwarding (Content Store, FIB, PIT), chunking,
repository, and NFN computation — that can be assembled into different node
types (forwarder, repository, NFN node).

## What this branch is doing

Branch `modernization/asyncio-python314` carries a **modernization effort**, not
a feature. See [`docs/modernization.md`](docs/modernization.md) for the full
phased plan. In short:

1. Bring the codebase to current Python (3.6-era → 3.14).
2. Replace the `multiprocessing`-per-layer architecture with `asyncio`.
3. Migrate the test suite from `nose` (unmaintained) to `pytest`, and fill
   coverage gaps so the migration is verifiable.

Changes here are intended to be **contributable upstream**. Keep commits
focused, keep the public API stable where possible, and document behavioural
changes explicitly.

## Ground rules

**Do not change network behaviour while changing architecture.** The migration
must preserve observable protocol behaviour — packet formats, forwarding
semantics, PIT/FIB/CS logic. If a behavioural change appears necessary, stop and
flag it rather than absorbing it into a refactor commit.

**Establish the baseline before changing anything.** Phase 0 of the plan exists
to record which tests pass *before* migration. Do not begin architectural work
until that baseline exists — otherwise "did we break it?" is unanswerable.

**One layer at a time.** The layer stack is a chain of independently runnable
components. Migrate and verify them individually; do not attempt a
whole-stack rewrite in a single change.

**Prefer deletion over accumulation.** A large part of this migration's value is
removing platform-specific branches and workarounds (three run-loop variants,
`select`/`poll` handling, Windows busy-wait fallbacks, call-stack inspection).
When async makes something unnecessary, delete it rather than keeping it "just
in case".

**Do not reach into private APIs.** The current code accesses
`multiprocessing.Queue._reader` to obtain file descriptors. Replacements must
not introduce equivalent private-API dependencies.

## Repository layout

```
PiCN/
  LayerStack/      # LayerStack: assembles layers, wires queues between them
  Processes/       # PiCNProcess / LayerProcess: per-layer execution model
  Layers/          # One subpackage per layer, each with its own test/ folder
    LinkLayer/         # Faces/interfaces (UDP, simulation), FaceIDTable
    PacketEncodingLayer/   # Wire format <-> Python objects (NDN TLV)
    ICNLayer/          # Content Store, FIB, PIT, forwarding logic
    ChunkLayer/        # Segmentation of large objects
    RepositoryLayer/   # Persistent content storage
    NFNLayer/          # Named Function Networking: parser, optimizer, executor
    ThunkLayer/        # Execution state management
    RoutingLayer/, AutoconfigLayer/, TimeoutPreventionLayer/
  ProgramLibs/     # Node types assembled from layers (ICNForwarder, NFNForwarder, ...)
  Packets/         # Packet types (Interest, Content, Nack) and names
  Mgmt/            # Management interface
  Simulations/     # Multi-node simulation scenarios
docs/              # Project documentation
starter/           # CLI entry points (picn-relay, picn-fetch, ...)
```

## Development setup

Until Phase 1 lands, the project has no dependency declaration beyond a single
`requirements.txt` entry. Work from a virtual environment:

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -e . pytest
```

`opencv-python` is pinned in `requirements.txt` but is only needed for
unrelated data-offloading demos. It does not build on current Python and should
not be treated as a required dependency.

## Running tests

The test suite predates this effort and targets `nose`, which does not run on
modern Python. Use `pytest`, which can generally collect the existing
`unittest`-style tests:

```bash
python -m pytest PiCN/ -q
```

Run a single layer's tests while working on it:

```bash
python -m pytest PiCN/Layers/ICNLayer/ -q
```

Some tests start real processes and bind sockets. Expect them to be slower and
more environment-sensitive than pure unit tests; this is one of the problems the
migration addresses.

## Code conventions

- **Type hints**: partially present today (e.g. `LayerStack.py`,
  `BasicICNLayer.py`). Add them to code you touch; do not embark on a
  repo-wide typing sweep as part of an unrelated change.
- **Docstrings**: existing style is Sphinx-flavoured with `:param:` / `:return:`
  / `:raises:`. Match it in the modules you edit.
- **Comments**: explain *why*, not *what*. Non-obvious invariants — especially
  ordering or concurrency assumptions surfaced by this migration — are worth a
  line; restating the code is not.
- **Commit scope**: one concern per commit. "Migrate ICNLayer to asyncio" and
  "fix escape sequences repo-wide" are separate commits.

## Things that will surprise you

These are real properties of the current codebase, verified by reading it:

- `LayerProcess` has **three** run-loop implementations — `_run_poll`
  (`select.poll`), `_run_select` (`select.select`), and `_run_sleep` (a 0.3 s
  busy-wait loop for Windows, which lacks `select` on non-socket descriptors).
- Which loop runs is chosen partly by `in_unittest()`, which **inspects the call
  stack** for the strings `"unittest"` or `"nose"`. It does not know about
  `pytest`, so it returns `False` under pytest and selects the `select`-based
  loop — which carries a 1024 file-descriptor ceiling that constrains
  simulation size.
- `BasicLinkLayer` overrides all three run loops to multiplex socket file
  descriptors alongside the inter-layer queues.
- `BaseInterface.file_descriptor` exists specifically so the link layer can
  `select()` on interfaces. This contract changes under asyncio.
- `PiCNProcess` implements `__getstate__`/`__setstate__` to drop the logger when
  pickling — required because `multiprocessing` must serialise layer objects.
  Under asyncio, nothing is pickled and this machinery becomes unnecessary.
- `stop_process()` calls `terminate()` and then `sleep(0.1)` twice around queue
  teardown — timing-dependent shutdown that async cancellation replaces cleanly.
