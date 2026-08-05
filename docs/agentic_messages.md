# Agentic layer — message formats

## Capability names

Wire form (all construction via `agentic.agentic_layer.naming`):

```
/cap/<issuer-digest>/<capability-path...>/v=<version>
```

* `cap` — fixed marker  
* `issuer-digest` — lowercase unpadded base32(SHA-256(issuer SPKI DER))  
* path — one or more components  
* `v=` — version marker prefix  

Longest-prefix match for the C-FIB **excludes** the version component.

## Signed artefacts

Shared envelope (`agentic.trust.signed_artefact`):

* Canonical body: JCS (RFC 8785-style), no floats in signed bodies  
* Signature: Ed25519 over the canonical bytes  
* Kinds include: `capability-descriptor`, `task-graph-template`,
  `agentic-steer`, accountability-log entries  

## Capability descriptor (body fields)

| Field | Notes |
|---|---|
| `kind` | `capability-descriptor` |
| `domain`, `task`, `version` | Identity |
| `constraints` | e.g. jurisdiction |
| `attestation_policy` | `required` / `optional` / `none` |
| `reputation_threshold`, `cost` | Decimal **strings** (not floats) |
| `input_schema`, `output_schema` | Restricted JSON Schema profile |
| `freshness_bound_s` | Integer seconds |
| `revocation_pointer` | Opaque string |

## Steer

`AgenticSteer` may mutate only an allow-list: `payload` and
`constraints.latency_bound`. Changing the expected sub-intent set, aggregation
policy, capability, or credential is rejected wholesale.

## Trace leaves

Each Context PIT leaf:

```
leaf = SHA-256(sub_intent_digest || steer_chain_head || response_digest)
```

Sentinels (domain-separated hashes, not zero bytes):

* `NULL_RESPONSE`, `NULL_STEER`, `PENDING`

Merkle tree: RFC 6962 (SHA-256, `0x00` leaf / `0x01` internal prefixes).

## Attestation responses

Responses reference attestation by `quote_id` only. Quotes prove which binary
ran recently — **not** that a capacity claim is true.
