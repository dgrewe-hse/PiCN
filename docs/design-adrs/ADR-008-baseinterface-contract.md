# ADR-008: Interfaces push inbound data; no file descriptors

- **Status:** Accepted
- **Date:** 2026-08-03
- **Phase:** 3
- **Relates to:** ADR-003, ADR-004, ADR-005

## Context

`PiCN/Layers/LinkLayer/Interfaces/BaseInterface.py` defines three abstract
members:

```python
def send(self, data, addr): ...
def receive(self): ...

@property
def file_descriptor(self): ...
```

The docstring on `file_descriptor` states its purpose plainly: *"This property is
used by the LinkLayer to multiplex different sockets."* It exists solely so
`BasicLinkLayer` can `select()` across sockets **and** inter-layer queues at
once — which is why `BasicLinkLayer` overrides all three run loops.

`UDP4Interface` implements `receive()` with a blocking `socket.recvfrom`.

Under asyncio, `select()`-based multiplexing is the event loop's job. The
`file_descriptor` contract therefore has no remaining purpose — and worse, it
forces every interface to expose one, which not every transport has.

This is a **public extension point**: third parties may have implemented
`BaseInterface`. The change must be deliberate and documented.

## Options

### A — Keep `file_descriptor`, register FDs with the loop

| Pros | Cons |
|---|---|
| Smallest change to the interface contract | Retains an abstraction that only makes sense for FD-backed transports |
| | `loop.add_reader` is unavailable on some platforms and for some transports |
| | Keeps the link layer doing multiplexing the loop should own |

### B — `async def receive()` — the link layer awaits each interface

| Pros | Cons |
|---|---|
| No file descriptors in the contract | The link layer must run one awaiting task per interface |
| Any transport can implement it | Slightly more task bookkeeping |

### C — Interfaces push inbound data into a queue

| Pros | Cons |
|---|---|
| Fits asyncio's datagram-protocol model directly | Interfaces need a queue reference at construction |
| The link layer awaits **one** queue regardless of interface count | Ownership of the queue must be clear |
| Backpressure works uniformly (ADR-005) | |
| No per-interface polling task | |

## Decision

**Option C.** Interfaces receive a queue and **push** inbound
`(data, address, interface_id)` into it. `send()` becomes `async def`.
`file_descriptor` is removed from the contract.

This matches how asyncio actually delivers datagrams —
`loop.create_datagram_endpoint` invokes a protocol callback — so the UDP
implementation becomes a natural fit rather than an adaptation.

## Consequences

- **Breaking change** to a public extension point. Document it in
  `docs/architecture.md` and the release notes with a migration note.
- `BasicLinkLayer` loses all three run-loop overrides; it awaits a single
  inbound queue.
- `UDP4Interface` is rewritten on `loop.create_datagram_endpoint`, preserving
  broadcast support (`enable_broadcast`, `get_broadcast_address`).
- The simulation interface becomes trivial — it already delivers in memory.
- The 1024-descriptor `select()` ceiling disappears, so simulations can scale
  further.

## Rules for implementers

1. New contract:
   ```python
   async def send(self, data, addr) -> None: ...
   def set_inbound_queue(self, queue) -> None: ...
   # file_descriptor: REMOVED
   ```
2. Interfaces push tuples of `(data, address, interface_id)`. The link layer
   must be able to tell which interface delivered a datagram.
3. Push with `await queue.put(...)` so bounding applies (ADR-005). Do not use
   `put_nowait`.
4. **Do not** keep `file_descriptor` "for compatibility". A vestigial property
   invites new code to depend on it. Remove it.
5. **Do not** call blocking `socket.recvfrom` or `socket.sendto` anywhere. Use
   the datagram transport.
6. Preserve broadcast behaviour exactly — `enable_broadcast()` returning `False`
   by default is existing, intended behaviour for interfaces without support.
7. Port one interface at a time: `UDP4Interface` first, then the simulation
   interface, verifying between them.

## Verification

No file-descriptor contract remains:

```bash
grep -rn "file_descriptor" PiCN/ --include=*.py | grep -v test
```

Expect **empty output**.

No blocking socket calls in the link layer:

```bash
grep -rn "recvfrom\|sendto\|select\." PiCN/Layers/LinkLayer/ --include=*.py | grep -v test
```

Expect **empty output**.

Two nodes exchange real packets — the Phase 3 exit criterion:

```bash
.venv/bin/python -m pytest PiCN/Layers/LinkLayer PiCN/ProgramLibs/ICNForwarder -q --timeout=90
```
