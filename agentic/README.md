# Agentic package

# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause

Capability-routing layer that sits above Named Function Networking. The
package is **substrate-agnostic**: mechanisms talk to a narrow port interface;
only the PiCN adapter imports the existing stack. Agent frameworks (for
example Pydantic AI) stay behind the binding package so the forwarding path
never depends on an LLM client.

This keeps the door open to run the same layer on another forwarder later
without rewriting trust, scenario, or measurement code.

## License

BSD 3-Clause, same as the rest of this repository. See the root
[`LICENSE`](../LICENSE) file. Every source file under `agentic/` carries a
copyright notice and `SPDX-License-Identifier: BSD-3-Clause`.

## Layout

```
agentic/
  port/              # SubstratePort Protocol + event types (no PiCN imports)
  adapters/
    picn/            # the ONLY module importing PiCN.*
    mock/            # in-memory adapter for tests
  agentic_layer/     # async-only layer: C-FIB, decomposer, Context PIT
  trust/             # attestation, reputation, accountability log
  binding/           # capability backends (deterministic + agent frameworks)
  scenario/          # end-to-end actors and world-state simulator
  benchmark/         # sweep runner, metrics, rollups
  tests/
```

## Architectural contracts

Enforced by `tests/test_architecture.py` (Agentic CI runs them first):

| ID | Rule |
|---|---|
| **AC1** | `trust/`, `scenario/`, `benchmark/` import nothing from `PiCN.*` |
| **AC2** | Only `adapters/picn/` imports `PiCN.*` |
| **AC3** | No module on the forwarding path imports an LLM client (`pydantic_ai`, `openai`, `anthropic`, `ollama`) |
| **AC4** | `port/` defines no substrate-specific types |
| **AC5** | Agentic modules import the port interface, never adapter internals |
| **AC6** | `binding/` imports attestation verification and reference APIs only — never minting |

## Development

Python 3.14, from the repository environment:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Optional LLM binding extras (not required for the forwarding path):

```bash
pip install -e ".[agentic]"
```

### Tests

```bash
# Architectural contracts + typing
python -m pytest agentic/tests/test_architecture.py -v --timeout=30
mypy --strict agentic/

# Full agentic suite (grows with each milestone)
python -m pytest agentic/ -v --timeout=90
```

Continuous integration for this package lives in
[`.github/workflows/agentic-ci.yml`](../.github/workflows/agentic-ci.yml) and
is separate from the PiCN stack workflow (`ci.yml`).

## Status

Package skeleton and architectural enforcement only. Mechanisms (port,
adapters, layer, trust, binding, scenario, harness) land in follow-up work.
