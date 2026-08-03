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

## What `interface_id` is

**It already exists in the codebase.** `BasicLinkLayer.data_from_lower` derives
it today as the interface's **positional index in the link layer's `interfaces`
list**:

```python
addr_info = AddressInfo(addr, self.interfaces.index(interface))
```

and `data_from_higher` uses it to select the outbound interface:

```python
self.interfaces[addr_info.interface_id].send(packet, addr_info.address)
```

`AddressInfo.__init__(self, address, interface_id: int)` already carries this
contract, and `FaceIDTable` maps `AddressInfo` to face IDs. **None of that
changes.**

What *does* change: today the link layer *derives* the index, because its
`select()` loop knows which interface fired. Under a push model the interface
must supply it — so the link layer **assigns** the index at registration and the
interface stores and echoes it back.

```python
# BasicLinkLayer, during setup:
for index, interface in enumerate(self.interfaces):
    interface.register(self._inbound_queue, interface_id=index)
```

Do **not** use `id(self)`, `uuid`, or any other identity scheme. The index is
what `AddressInfo` and `FaceIDTable` already expect, and changing it would
break face resolution throughout the link layer.

## Migration path for third-party implementations

`BaseInterface` is a public extension point. Removing `file_descriptor` and
changing `send`/`receive` breaks any external implementation, so the break is
made **loud and recoverable** rather than silent:

1. **`file_descriptor` is retained as a property that raises**
   `NotImplementedError` with a message naming this ADR. An external
   implementation then fails immediately with an explanation, instead of
   mysteriously never receiving data.
2. **Ship `LegacySyncInterfaceAdapter`**, wrapping an old-style interface
   (blocking `receive()`, synchronous `send()`) and driving it via
   `run_in_executor` (ADR-009). Existing implementations keep working unchanged
   behind the adapter, at a performance cost.
3. **Document the change** in `docs/architecture.md` and the release notes, with
   a before/after example.

The adapter is a migration aid, not a supported long-term path — mark it
deprecated on introduction.

## Rules for implementers

1. New contract:
   ```python
   async def send(self, data, addr) -> None: ...
   def register(self, queue: asyncio.Queue, interface_id: int) -> None: ...

   @property
   def file_descriptor(self):        # retained, raises
       raise NotImplementedError(
           "file_descriptor was removed in the asyncio migration; "
           "see docs/design-adrs/ADR-008-baseinterface-contract.md"
       )
   ```
2. Interfaces push `(data, address, interface_id)`, where `interface_id` is the
   value received in `register()` — see the section above. Never invent one.
3. Push with `await queue.put(...)` so bounding applies (ADR-005). Do not use
   `put_nowait`.
4. `file_descriptor` **is not deleted outright** — it raises with a pointer to
   this ADR. Deleting it silently would make third-party breakage
   undiagnosable.
5. **Do not** call blocking `socket.recvfrom` or `socket.sendto` anywhere. Use
   the datagram transport.
6. Preserve broadcast behaviour exactly — `enable_broadcast()` returning `False`
   by default is existing, intended behaviour for interfaces without support.
7. Port one interface at a time: `UDP4Interface` first, then the simulation
   interface, verifying between them.
8. Do not change `AddressInfo` or `FaceIDTable`. Their contracts are unaffected.

## Verification

No interface *uses* a file descriptor, and the only remaining mention is the
raising stub:

```bash
grep -rn "file_descriptor" PiCN/ --include=*.py | grep -v test
```

Expect exactly **one hit** — the `NotImplementedError` property on
`BaseInterface`. Any other hit means a caller still depends on it.

Every interface receives its id rather than deriving one:

```bash
grep -rn "def register" PiCN/Layers/LinkLayer/Interfaces/ --include=*.py
grep -rn "id(self)\|uuid" PiCN/Layers/LinkLayer/ --include=*.py | grep -v test
```

Expect a `register` on each interface, and **empty output** for the second — no
invented identity schemes.

No blocking socket calls in the link layer:

```bash
grep -rn "recvfrom\|sendto\|select\." PiCN/Layers/LinkLayer/ --include=*.py | grep -v test
```

Expect **empty output**.

Two nodes exchange real packets — the Phase 3 exit criterion:

```bash
.venv/bin/python -m pytest PiCN/Layers/LinkLayer PiCN/ProgramLibs/ICNForwarder -q --timeout=90
```
