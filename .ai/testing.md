# WorkPulse — Testing strategy

> What "verified" means in this repo. Gates are commands, not opinions.

## Gates

| Gate | Command | Covers |
| --- | --- | --- |
| all | `make check` | both gates below — run before claiming done |
| API | `make check-api` | Ruff + Pyright + `uv run pytest` in `apps/api` (ADR 0006) |
| Web | `make check-web` | `tsc --noEmit` + ESLint + Vitest in `apps/web` |
| E2E | `make check-e2e` | Playwright against web + api + seeded dev db — not in `make check` until fast and stable (decision 3) |
| Load | Phase 7 | throughput/latency under realistic check-in load |

`make check` grows as phases land (contract validation, e2e, lint). A gate that
is not wired into `make check` does not protect anything.

## Test levels

| Level | Where | Needs | Purpose |
| --- | --- | --- | --- |
| Unit | `apps/api/tests/unit` | nothing (no DB, no network) | domain services, policies, state machines, pure helpers |
| Integration | `apps/api/tests/integration` | PostgreSQL (docker compose) | repositories, migrations, tenant scoping, routers end-to-end in-process |
| Contract | `apps/api/tests/contract` | OpenAPI + `contracts/events/*` | API shape and event payloads match the published contracts |
| Web | `apps/web/tests` | runner TBD (proposed: Vitest) | feature hooks/components, error states |
| E2E | `tests/e2e` | web + api + db | one feature, one user-visible flow, both surfaces |
| Performance | `tests/performance` | Phase 7 | latency/throughput budgets |

## Mandatory rules

1. **Every feature**: happy path **and** at least one failure case on every
   surface it touches.
2. **Every domain**: a tenant-isolation test — data of tenant A is invisible to
   tenant B (expect 404, not 403).
3. **Every mutating feature**: an audit-entry assertion and a denied-role test.
4. `tests/unit` never requires PostgreSQL, Redis, Kafka, or network. If a unit
   test needs I/O, it belongs in integration.
5. **Time is injected.** Attendance, lateness and payroll tests use a clock
   parameter, never wall time; no `datetime.now()` inside assertions.
6. Seeds and fixtures are deterministic (fixed ids, fixed timestamps). Synthetic
   people only.
7. Assert behavior and stable error codes, not framework internals or log text.
8. A red gate blocks "done". Fix the cause, not the test — unless the test is
   wrong, and then say why in one sentence.

## What each layer must prove

- **Domain services** — business rules and invariants (duplicate check-in,
  cycle in department tree, transfer appends history, terminated employee cannot
  check in).
- **Policies** — who may do what (role matrix rows from `.ai/security.md`).
- **Repositories** — tenant scoping, pagination/filter correctness, no leaked
  connections.
- **Routers** — status codes, error shape `{"error": {"code", "message"}}`,
  request/response schemas.
- **Contracts** — every event emitted validates against its versioned schema;
  every route the web calls exists with the shape the UI expects.
- **Web features** — loading, empty, error states; no ad-hoc `fetch`.
- **E2E** — the flow a user actually performs (e.g. check in → appears on the
  manager board).

## Coverage expectations

- `domains/*/service.py` and `policies.py`: high — this is the product.
- Routers: thin, covered by contract/integration tests.
- Web: at least happy path + error state per feature once the runner is chosen.
- Coverage percentages are not a gate; untested business rules are.

## Decisions taken (2026-09-24)

1. **Web runner: Vitest + Testing Library** in `apps/web`, wired into
   `make check-web` alongside `tsc --noEmit`.
2. **Integration database: a dedicated `postgres-test` compose service** rather
   than testcontainers — simpler locally, no Docker-in-Docker, and
   `make check-api` can bring it up deterministically.
3. **E2E: Playwright** in `tests/e2e/`, behind its own `make check-e2e`. It joins
   `make check` only once it is fast and stable enough to gate every change —
   a flaky gate gets ignored, which is worse than no gate.