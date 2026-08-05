# Agentic layer — configuration

## Runtime

Agentic nodes require:

```python
runtime="async"   # or Runtime.ASYNC
```

Sync is refused explicitly (`SyncRuntimeNotSupported`).

## Model preference (Pydantic AI)

TOML file, local-first / test-first:

```toml
[model]
preference = ["test", "ollama:llama3.2", "openai:gpt-4o-mini"]
```

* First entry wins for construction.  
* `"test"` / `"test:…"` selects Pydantic AI `TestModel` (no network).  
* Do not pin model ids in Python source.

Pass the path to `PydanticAIBackend(model_config_path=...)`.

## Measurement harness

Required run metadata (hard refuse if missing):

| Field | Values |
|---|---|
| `seed` | integer |
| `transport` | `"bus"` or `"udp"` |

Also:

* Dirty working tree refused unless `allow_dirty=True` (stamps
  `reproducible=False`).  
* Bus runs: `executor_workers > simulated_interfaces` or refuse naming A-011.  
* Rollups always group by `transport` — never mix.

Example:

```python
from agentic.benchmark import MeasurementHarness, RunConfig

result = await MeasurementHarness().run(
    RunConfig(seed=42, transport="bus", k=3, allow_dirty=True,
              executor_workers=8, simulated_interfaces=3)
)
```

## Optional extras

```bash
pip install -e ".[dev]"       # pytest, mypy, …
pip install -e ".[agentic]"   # pydantic-ai + cryptography
```

Cryptography is needed for signed artefacts; `pydantic-ai` only for the LLM
backend path.
