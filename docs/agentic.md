# Agentic layer — architecture

The `agentic/` package adds **capability routing** above Named Function
Networking (NFN). It is substrate-agnostic: mechanisms talk to a narrow
[`SubstratePort`](agentic_port.md); only the PiCN adapter imports the existing
stack. Agent frameworks (for example Pydantic AI) stay behind the
[binding](agentic_bindings.md) package so the forwarding path never depends on
an LLM client.

## Placement

`AgenticLayer` sits **above `NFNLayer`**, topmost in an async node stack:

```
AgenticLayer
    ↓
NFNLayer → Chunk → TimeoutPrevention → ICN → PacketEncoding → Link
```

NFN claims only names carrying its marker; capability-named Interests pass
through upward unmodified. The agentic layer is **async-only** —
`runtime="async"` is required (`AgenticForwarder` refuses sync).

Use [`PiCN.ProgramLibs.AgenticForwarder`](../PiCN/ProgramLibs/AgenticForwarder/)
to assemble a node with the layer wired in.

## Package layout

```
agentic/
  port/              # SubstratePort Protocol + events (no PiCN imports)
  adapters/
    picn/            # ONLY broad PiCN.* imports
    mock/            # in-memory port for tests
  agentic_layer/     # AgenticLayer, C-FIB, decomposer, Context PIT, Steer
  trust/             # attestation, reputation, accountability log
  binding/           # CapabilityBackend (deterministic + Pydantic AI)
  scenario/          # pluggable scenarios (cardiac, …)
  benchmark/         # measurement harness
  tests/
```

## Architectural contracts

Enforced by `agentic/tests/test_architecture.py` (Agentic CI runs them first):

| ID | Rule |
|---|---|
| **AC1** | `trust/`, `scenario/`, `benchmark/` import nothing from `PiCN.*` |
| **AC2** | Only `adapters/picn/` imports `PiCN.*` broadly; `agentic_layer/` may use `PiCN.Processes` / `PiCN.Packets` only |
| **AC3** | No LLM client on the forwarding path (`pydantic_ai`, `openai`, `anthropic`, `ollama`) |
| **AC4** | `port/` defines no substrate-specific types |
| **AC5** | Agentic modules import the port interface, never adapter internals |
| **AC6** | `binding/` may verify/reference attestation — never mint quotes |

## Plugging an agent

1. Build a signed **capability descriptor** (see [messages](agentic_messages.md)).
2. Implement a [`CapabilityBackend`](agentic_bindings.md) (deterministic function
   or Pydantic AI with `preference = ["test"]` for offline/CI).
3. Call `AgenticLayer.register_capability(descriptor, backend)` or
   `AgenticForwarder.register_capability(...)`.

The fabric invokes every backend through the same
`CapabilityRegistry.invoke` path — no branching on backend type.

## Related docs

* [Port and events](agentic_port.md)
* [Bindings](agentic_bindings.md)
* [Message formats](agentic_messages.md)
* [Configuration](agentic_config.md)
* [Hello agent](hello_agent.md)
