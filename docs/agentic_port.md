# Agentic layer — substrate port

The agentic layer never talks to sockets or PiCN queues directly for its
substrate-neutral mechanisms. It uses a structural Protocol:

```python
from agentic.port.protocol import SubstratePort
```

Adapters push lifecycle **events** into a bounded `asyncio.Queue` owned by the
layer. Always `await queue.put(event)` — never `put_nowait`.

## Operation groups

| Group | Methods |
|---|---|
| Lifecycle | `start(inbound)`, `stop()` |
| Naming | `parse_name`, `name_from_components`, `longest_prefix_match` |
| Request/response | `send_request`, `send_response` |
| Forwarding table | `register_prefix`, `unregister_prefix`, `lookup` |

## Event union (closed)

| Event | Meaning |
|---|---|
| `RequestSent` | Substrate accepted a request |
| `ResponseArrived` | Matching response payload |
| `RequestTimedOut` | Deadline passed with no response |
| `RequestFailed` | Substrate failure (`SubstrateError` reason) |
| `InboundRequest` | This node should produce a response |

`correlation` is opaque bytes: the adapter echoes it and must not parse it.

## Implementations

| Adapter | Package | Use |
|---|---|---|
| **Mock** | `agentic.adapters.mock.MockSubstratePort` | Unit/integration tests, scenarios |
| **PiCN** | `agentic.adapters.picn.PicnSubstratePort` | Real async UDP Interest/Content client |

Attach a port with `AgenticLayer.attach_port(port)` then `await start_port()`.

## Errors

Two disjoint families:

* **`SubstrateError`** (+ `Unreachable`, `Malformed`, `TransportClosed`) — only
  inside `RequestFailed` events; adapters never raise into agentic code.
* **`AgenticError`** (+ `DescriptorInvalid`, `BoundExceeded`, …) — above the
  port only; adapters must not import them.
