# Hello agent

A minimal walkthrough: register a deterministic capability, serve an inbound
request on the mock port, then (optionally) start an `AgenticForwarder`.

**No LLM connection is required.**

## 1. Environment

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Register and serve (mock port)

```python
import asyncio
import json

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentic.adapters.mock import MockSubstratePort
from agentic.agentic_layer import AgenticLayer
from agentic.agentic_layer.descriptor import parse_descriptor_body
from agentic.binding import DeterministicBackend
from agentic.trust import generate_ed25519_private_key, sign_artefact, verify_artefact

IN = {
    "type": "object",
    "properties": {"patient_id": {"type": "string"}},
    "required": ["patient_id"],
    "additionalProperties": False,
}
OUT = {
    "type": "object",
    "properties": {"beds_free": {"type": "integer"}},
    "required": ["beds_free"],
    "additionalProperties": False,
}


async def main() -> None:
    key = generate_ed25519_private_key()
    body = {
        "kind": "capability-descriptor",
        "domain": "hospital",
        "task": "beds",
        "version": "1.0.0",
        "constraints": {"jurisdiction": "DE"},
        "attestation_policy": "optional",
        "reputation_threshold": "0.5",
        "cost": "1",
        "input_schema": IN,
        "output_schema": OUT,
        "freshness_bound_s": 60,
        "revocation_pointer": "none",
    }
    desc = parse_descriptor_body(
        verify_artefact(sign_artefact(body, key)),
        capability_path=(b"hospital", b"beds"),
    )

    async def beds(payload: dict) -> dict:
        return {"beds_free": 2}

    port = MockSubstratePort()
    layer = AgenticLayer(runtime="async", port=port)
    layer.register_capability(
        desc,
        DeterministicBackend(beds, input_schema=IN, output_schema=OUT),
        backend_label="deterministic",
    )
    await layer.start_port()
    corr = b"\x01" * 32
    await port.inject_inbound_request(
        desc.name,
        json.dumps({"patient_id": "demo"}).encode(),
        correlation=corr,
    )
    await asyncio.sleep(0.05)
    print(json.loads(port.responses_sent[corr]))
    await layer.stop_port()


asyncio.run(main())
```

Expected output shape:

```json
{"input_rejected": false, "output_valid": true, "payload": {"beds_free": 2}}
```

## 3. AgenticForwarder (async stack)

```python
from PiCN.ProgramLibs.AgenticForwarder import AgenticForwarder
from PiCN.ProgramLibs.runtime import Runtime

fwd = AgenticForwarder(port=0, runtime=Runtime.ASYNC, log_level=255)
# fwd.register_capability(desc, backend)
await fwd.start_forwarder_async()
# … use mgmt / faces …
await fwd.stop_forwarder_async()
```

## 4. Optional dual-backend (still offline)

With `pip install -e ".[agentic]"` and a TOML file:

```toml
[model]
preference = ["test"]
```

register the same descriptor with `PydanticAIBackend(..., model_config_path=...,
test_output={"beds_free": 2})`. The invoke path is identical.

## Next steps

* [Architecture](agentic.md)  
* [Bindings](agentic_bindings.md)  
* [Port](agentic_port.md)  
* [Demo and measurements](agentic_demo.md) · [`demo/`](../demo/)  
* Cardiac scenario: `python -m pytest agentic/tests/test_scenario_e2e.py -v`
* SimulationBus three-way: `python -m demo.run_paired_bus --seeds 1 --k 2 --allow-dirty`
