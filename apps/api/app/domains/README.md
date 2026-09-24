# Domains

One package per business domain, created when its feature spec lands
(`.ai/features/<domain>.md`). The pattern every domain follows:

| File | Meaning |
| --- | --- |
| `models.py` | persistence/domain model |
| `schemas.py` | input/output contracts |
| `service.py` | use cases — the only place business logic lives |
| `repository.py` | data-access abstraction |
| `events.py` | domain events emitted by this domain |
| `policies.py` | business rules / authorization |
| `exceptions.py` | domain-specific errors (`WorkPulseError` subclasses) |

Rules:

- A domain never imports `app.infrastructure.*` directly — repositories wrap I/O.
- A domain never imports another domain's internals; go through its service.
- Create a domain only from a spec, never speculatively.

Planned domains, in order: `employees`, `organizations`, `auth`, `attendance`,
`leave`, `payroll`, `recruitment`, `performance`, `analytics`, `alerts`.