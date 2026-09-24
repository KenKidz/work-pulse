# Contracts

The backbone of the system. Contracts are written **before** the code that
implements or consumes them, and they version independently from deployment.

| Kind | Location | Status |
| --- | --- | --- |
| Event schemas | `events/` | Phase 1 — envelope exists; domain events land with their features |
| OpenAPI (generated from the API) | `openapi/openapi.json` | generated when the first real endpoint group lands |

## Rules

- Every event uses `events/common/event-envelope.v1.json`.
- Within a version, changes are additive only. A breaking change means a new
  version file; old and new coexist while consumers migrate.
- Consumers must ignore unknown fields (forward compatibility) and deduplicate
  on `event_id` (idempotency).
- Sensitive employee data never appears in a payload beyond identifiers.
- Schema linting in `make check` arrives with the first event producer
  (Phase 3) — until then, contracts are reviewed by hand.