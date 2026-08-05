# Agentic package

# Copyright (c) 2026 Dennis Grewe, Hochschule Esslingen.
# SPDX-License-Identifier: BSD-3-Clause

Capability-routing layer that sits above Named Function Networking. The
package is **substrate-agnostic**: mechanisms talk to a narrow port interface;
only the PiCN adapter imports the existing stack. Agent frameworks (for
example Pydantic AI) stay behind the binding package so the forwarding path
never depends on an LLM client.

Public documentation lives under [`docs/`](../docs/):

* [Architecture](../docs/agentic.md)
* [Hello agent](../docs/hello_agent.md)
* [Bindings](../docs/agentic_bindings.md) · [Port](../docs/agentic_port.md) ·
  [Messages](../docs/agentic_messages.md) · [Config](../docs/agentic_config.md)

## License

BSD 3-Clause, same as the rest of this repository. See the root
[`LICENSE`](../LICENSE) file. Every source file under `agentic/` carries a
copyright notice and `SPDX-License-Identifier: BSD-3-Clause`.

## Layout

```
agentic/
  port/              # SubstratePort Protocol + event types (no PiCN imports)
  adapters/
    picn/            # the ONLY module importing PiCN.* broadly
    mock/            # in-memory adapter for tests
  agentic_layer/     # async-only layer: C-FIB, decomposer, Context PIT, plug-in
  trust/             # attestation, reputation, accountability log
  binding/           # capability backends (deterministic + agent frameworks)
  scenario/          # pluggable scenarios (cardiac, …)
  benchmark/         # sweep runner, metrics, rollups
  tests/
```

## Architectural contracts

Enforced by `tests/test_architecture.py` (Agentic CI runs them first):

| ID | Rule |
|---|---|
| **AC1** | `trust/`, `scenario/`, `benchmark/` import nothing from `PiCN.*` |
| **AC2** | Only `adapters/picn/` imports `PiCN.*` broadly; `agentic_layer/` may import `PiCN.Processes` / `PiCN.Packets` only |
| **AC3** | No LLM client on the forwarding path (`pydantic_ai`, `openai`, `anthropic`, `ollama`) |
| **AC4** | `port/` defines no substrate-specific types |
| **AC5** | Agentic modules import the port interface, never adapter internals |
| **AC6** | `binding/` imports attestation verification and reference APIs only — never minting |

## Development

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
python -m pytest agentic/tests/test_architecture.py -v --timeout=30
mypy --strict agentic/
python -m pytest agentic/ -v --timeout=90
```

Continuous integration: [`.github/workflows/agentic-ci.yml`](../.github/workflows/agentic-ci.yml).

## Status

Track 1 (M1–M6) mechanisms plus Phase G wiring:

* Port, mock + PiCN adapters, `AgenticLayer` plug-in API, `AgenticForwarder`
* Descriptors, C-FIB, decomposer, Steer, Context PIT, Merkle, aggregation
* Reputation, attestation, accountability log
* Capability backends (deterministic + Pydantic AI / `TestModel`)
* Cardiac scenario, measurement harness, exit-criteria invariant catalog

## Future work

* **Agentic management surface** — `AsyncMgmt` does not expose agentic state
  (capabilities, C-FIB, Context PIT, reputation, accountability log). Needed
  for operators; not part of Track 1. See `docs/agentic.md` and the private
  task plan future-work note.
