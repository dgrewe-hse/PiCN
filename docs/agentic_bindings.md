# Agentic layer — capability bindings

Backends implement one Protocol; the fabric cannot tell them apart:

```python
class CapabilityBackend(Protocol):
    def declared_schemas(self) -> tuple[dict, dict]: ...
    async def invoke(self, payload: dict, *, deadline: float) -> dict: ...
```

## Registration

The **descriptor** is authoritative. At registration the backend schemas are
checked for variance:

| Direction | Rule |
|---|---|
| Input | Backend must accept a **superset** of the descriptor input |
| Output | Backend must produce a **subset** of the descriptor output |

Unsupported JSON Schema constructs (`oneOf`, remote `$ref`, …) are refused at
registration. Runtime validation always uses the **descriptor** schemas.

```python
layer.register_capability(descriptor, backend, backend_label="deterministic")
```

## Deterministic backend

```python
from agentic.binding import DeterministicBackend

async def beds(payload: dict) -> dict:
    return {"beds_free": 3}

backend = DeterministicBackend(
    beds, input_schema=DESC_IN, output_schema=DESC_OUT
)
```

No LLM dependency. Preferred for CI and most scenario actors.

## Pydantic AI backend

Optional extra: `pip install -e ".[agentic]"`.

```python
from agentic.binding.pydantic_ai_backend import PydanticAIBackend

backend = PydanticAIBackend(
    input_model=HospitalIn,
    output_model=HospitalOut,
    model_config_path="model.toml",  # see agentic_config.md
    test_output={"beds_free": 3},
)
```

**No live LLM is required.** Use model preference `test` (Pydantic AI
`TestModel`) for offline demos and CI. Cloud/local model ids come only from
the config file — never hard-coded in source.

LLM client imports stay in `agentic/binding/` (AC3).

## Invocation path

1. Validate input against descriptor → reject as input failure if invalid  
2. `await backend.invoke(...)`  
3. Validate output against descriptor → `output_valid=False` on fault (reputation
   observation), not a crash  
4. Attach `quote_id` **by reference** only (never embed a quote)
