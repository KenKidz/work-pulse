# Feature: Deployment (Docker images, Kubernetes, CI/CD)

- **Status:** approved
- **Phase:** 8
- **Size:** M
- **Surfaces:** api+web+events+workers (the template ladder's only value
  covering every deployed process; no product surface changes — the real work
  is container images, Kubernetes manifests, the kind deploy proof and the
  GitHub Actions pipeline, plus one new meta route `GET /readyz`)
- **Links:** `.ai/architecture.md` (Phase roadmap row 8: `Docker images,
  Kubernetes, CI/CD` — "deployment and operations become real problems") ·
  `tasks/doc.md` §11 V8 · `.ai/testing.md` (gates table — "a gate that is not
  wired into `make check` does not protect anything", now extended to CI) ·
  `.ai/security.md` (Secrets & configuration) · ADR 0002 (modular monolith —
  one deployable API) · ADR 0006 (backend stack) · ADR 0023 (observability;
  worker metrics listeners bind loopback) · ADR 0024 (CDC: Kafka Connect /
  Debezium runs alongside the apps) · ADR 0025 (reliability: **breaker state is
  per-process — v1 deployment is one process per role**; k6-in-CI risk) ·
  `.ai/features/reliability.md` · `.ai/features/observability.md` ·
  `.ai/features/change-data-capture.md`

## Problem

WorkPulse is complete as a product but unshippable as software: every surface
runs as a native host process (`make dev` shells out to uvicorn, `next dev` and
`python -m workers.<name>.main`) over docker-compose infrastructure. There are
no deployable artifacts, no cluster manifests and no pipeline — a "release" is
`git pull` on a developer machine, and nothing proves the stack runs anywhere
except that machine. Phase 8 (brief V8) exists so deployment and operations
become real, verified problems: versioned container images, a Kubernetes
deployment proven end-to-end on kind with the full stack, and a CI pipeline
that runs every gate before anything is shipped.

## Business rules

1. **Exactly two images.** One Python image (`workpulse-api`) holding
   `apps/api` + `workers/` — the API is the default command, each worker is the
   same image with a command override (`python -m workers.<name>.main` for
   relay, realtime, notifications, payroll, cdc_sink) — and one web image
   (`workpulse-web`, Next.js `output: "standalone"`). One image per process is
   rejected (build/version surface for zero isolation gain in a modular
   monolith, ADR 0002). Images run as a non-root user, are tagged with the
   commit SHA (`sha-<short>`), and carry no secrets or `.env` files.
2. **Single replica per process — hard constraint.** Circuit-breaker state is
   in-memory per process (ADR 0025 §5), so every app workload deploys exactly
   one replica; scaling any app process beyond 1 is out of scope until shared
   breaker state exists. Infrastructure services are single-node dev-grade for
   the same reason (a kind proof, not a production HA topology).
3. **Cluster target is a local kind proof over the full stack.** The manifests
   deploy everything WorkPulse needs — api, web and the five workers, plus
   PostgreSQL, Redis, Kafka, ClickHouse, MinIO and Kafka Connect (Debezium, CDC
   readiness) as single-node in-cluster services — into a kind cluster. The
   proof is a **command** (`make deploy-check`), never a console click-through.
   Real-cloud clusters, managed services and multi-node topologies are out of
   scope.
4. **Migrations gate the rollout — by apply ordering.** Alembic runs as a
   one-shot Kubernetes Job that the deploy tooling (`make deploy-check` and
   the runbook) applies and awaits to completion **before applying any app
   workload** (api, web, workers) — infra up first, then the Job, then the
   apps. On a fresh cluster no app pod starts until migrations succeed; on an
   upgrade a failing migration means the new Deployments are never applied and
   the previous deployment keeps serving. The gate is this ordering, not
   `GET /readyz` — readiness reports PostgreSQL reachability, not migration
   state. Migrations stay append-only **and backward-compatible with the
   previously deployed image** (conventions), so rollback is image-rollback
   only — never a schema downgrade.
5. **CI runs every gate.** GitHub Actions (`.github/workflows/`) runs the full
   battery from `.ai/testing.md` on every push/PR — `make check-api`,
   `make check-web`, `make check-e2e` and `make check-load` — and merges to
   `main` additionally build and push both images to GHCR tagged with the
   commit SHA. A failing gate fails the pipeline and blocks the image push;
   there is no "green enough". If k6 cannot run on the CI runner, the gate's
   placement gets a PO revisit (ADR 0025 consequence) recorded in this spec —
   it is never silently skipped (a missing binary already fails loudly).
6. **Secrets reach the cluster out-of-band.** All configuration keeps the
   existing env-var/`Settings` model, populated from Kubernetes Secrets that
   are **applied from outside git** (a local untracked values file or manual
   `kubectl apply`; the manifests ship a documented placeholder + key list).
   No deployed-environment secret values in the repo, images, manifests or
   logs; the tracked dev-only credentials (`docker-compose.yml`,
   `.env.example`) are explicitly dev-only and never reused in a deployed
   environment (`.ai/security.md`).
7. **Probes: liveness ≠ readiness.** `GET /healthz` stays liveness-only (no
   dependencies). The new `GET /readyz` checks PostgreSQL with a bounded
   timeout (`READYZ_TIMEOUT_SECONDS`, default 2s) and gates pod readiness.
   Both are unauthenticated because kubelet
   probes carry no credentials; `/readyz` exposes only a status code and the
   house error code `dependency_unavailable` — never dependency details, never
   data. `GET`s are unthrottled (ADR 0025 tier table), so no rate-limit change
   is needed.
8. **Deployment does not disturb local development.** `make dev` and the whole
   compose-based gate flow stay exactly as they are; deployment adds new
   targets (`make images`, `make deploy-check`, …) and new files under
   `ops/deploy/` and `.github/`. The observability stack stays the local
   dev/CI tool it is today.

## Scope

- `apps/api/Dockerfile` + `apps/web/Dockerfile` (+ `.dockerignore`s): pinned
  base images, non-root runtime, the web app's `output: "standalone"`.
- `GET /readyz` on the API (PostgreSQL-backed readiness), wired to k8s probes.
- Kubernetes manifests under `ops/deploy/k8s/`: namespace, Deployments for
  api/web/relay/realtime/notifications/payroll/cdc-sink (1 replica each),
  single-node stateful infra (postgres, redis, kafka, clickhouse, minio,
  kafka-connect) with persistent volumes, Services, liveness/readiness probes,
   resource requests/limits, a migration Job (applied and awaited before the
   app workloads), and a Secret placeholder documenting the required keys.
- A kind-based deploy proof command (`make deploy-check`): create/reuse a kind
  cluster, load both images, apply the infra and await it, apply the migration
  Job and await completion, then apply the app workloads and wait for every
  workload to be Ready, then smoke the deployed stack (login → check-in visible
  on the board) — plus an upgrade-path scenario (deploy SHA n−1, attempt SHA n
  with a failing migration, assert n−1 keeps serving).
- GitHub Actions: a gates workflow (all four gates) and image build/push to
  GHCR on `main` (`sha-<short>` tags).
- A short deploy runbook (`ops/deploy/README.md`): kind bring-up, secret
  application order, migration Job, rollback = redeploy previous SHA.
- **ADR 0026** recording: two-image layout, single-replica constraint (ADR
  0025), kind-as-proof target (no cloud), GHCR + full-gate CI, out-of-band
  Secrets.

## Out of scope

- Real-cloud clusters (EKS/GKE/AKS/…), managed databases/brokers, or any
  provider-specific tooling (Terraform, cloud CLIs).
- Horizontal scaling of app processes, autoscaling, multi-replica breaker
  state (ADR 0025 explicitly defers shared state).
- HA infrastructure (multi-node PostgreSQL/Kafka, failover), backups/restore,
  disaster recovery.
- In-cluster observability (Prometheus/Grafana stay the local `ops/observability`
  stack; ADR 0023's loopback worker metrics listeners are unchanged — cluster
  scraping would need a bind-address decision and is a separate spec).
- GitOps controllers (Argo/Flux), sealed-secrets/SOPS or external secret
  managers (PO chose plain out-of-band Secrets).
- Ingress, TLS/certificate management, DNS — the smoke reaches the stack
  through kind port mappings/port-forward.
- Blue/green, canary, zero-downtime guarantees beyond Deployment defaults.
- Image provenance/signing (cosign), SBOM tooling, base-image CVE scanning
  policy — nice, not required by the phase gate.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API image | `apps/api/Dockerfile` (+ `apps/api/.dockerignore`) |
| Web image | `apps/web/Dockerfile` (+ `apps/web/.dockerignore`, `next.config.ts` `output: "standalone"`) |
| Readiness route | `apps/api/app/main.py` (alongside `GET /healthz`; `/readyz` + Settings-backed probe) |
| Kubernetes manifests | `ops/deploy/k8s/` (Deployments, Services, stateful infra, migration Job, Secret placeholder) |
| Deploy proof + runbook | `Makefile` targets (`images`, `deploy-check`, …) · `ops/deploy/README.md` · `ops/deploy/` scripts |
| CI | `.github/workflows/` (gates workflow; image build/push to GHCR on `main`) |
| Events | none — `contracts/` unchanged |
| Workers | no worker code changes; each `workers/<name>/` gains only a container command line |
| API tests | `apps/api/tests/unit` (readyz dependency logic) · `tests/integration` (readyz against real PostgreSQL + failure) |
| E2E / smoke | `tests/e2e/` unchanged (local gates) · deploy smoke lives with `ops/deploy/` |

## Roles & access

- No user-facing or data surface changes; the permission matrix in
  `.ai/security.md` is untouched.
- `GET /readyz` is unauthenticated (kubelet probes carry no credentials) and
  returns only 200/503 + the house error shape. It exposes no tenant data, no
  dependency names beyond the generic `dependency_unavailable` code, and is a
  `GET` so the rate limiter (ADR 0025) leaves it unthrottled like `/healthz`.
- The GHCR registry and the kind cluster are developer/CI infrastructure, not
  a tenant surface.

## API contract

One new meta route; everything else is frozen as shipped.

| Route | Auth | Success | Failure |
| --- | --- | --- | --- |
| `GET /healthz` | none (unchanged) | `200 {"status":"ok"}` — no dependencies | — |
| `GET /readyz` | none (probe requirement) | `200 {"status":"ready"}` — PostgreSQL reachable within `READYZ_TIMEOUT_SECONDS` (default 2s) | `503 {"error":{"code":"dependency_unavailable","message":"…"}}` |

No request body, no query params, no tenant scoping (no data read). The 503
uses the house error shape from `.ai/conventions.md` — never raw driver
errors.

## Events

None. `contracts/` is untouched: deployment publishes no domain events and
changes no event payload.

## Edge cases

- **Migration failure on a fresh cluster:** the app workloads are never
  applied (the Job gate blocks them), so nothing becomes Ready; the smoke
  fails naming the migration Job and its exit state. On an upgrade, the new
  Deployments are never applied and the old deployment keeps serving.
- **Secret missing at boot:** app containers fail configuration validation
  naming the missing key (plain-text "missing configuration: `<KEY>`", never
  the value) and readiness never passes; the smoke names the missing key.
- **Secret wrong-but-present:** boot succeeds and the first dependent call
  fails; the surfaced error names the key only — never the value, never raw
  driver/DSN text.
- **Images not loaded into kind:** pods sit in `ImagePullBackOff`; the smoke
  times out and prints pod status instead of declaring success.
- **A worker crash-loops:** the Deployment restarts it; the smoke asserts every
  workload Ready before it runs, so a crash-looping worker fails the proof.
- **k6 unavailable on the CI runner:** the load gate fails loudly (ADR 0025);
  the downgrade path is a PO decision, recorded in this spec's decisions, not a
  silent skip.
- **CI disk/time pressure** (Playwright browsers + k6 + five infra containers):
  if the runner cannot sustain the e2e/load gates, the pipeline fails visibly
  and the gate placement returns to the PO — same rule as above.
- **Readiness flapping during dependency blips:** `/readyz` is bounded by
  `READYZ_TIMEOUT_SECONDS` (default 2s) so a slow query cannot hang the probe;
  probe thresholds absorb single blips before restarts.
- **Rollback after a bad release:** redeploy the previous SHA's images; schema
  is never downgraded (migrations append-only), so the previous code runs
  against the newer schema — safe exactly while migrations stay
  backward-compatible with the previous image, the expand-only rule in
  `.ai/conventions.md` keeps that true.

## Acceptance criteria

- [ ] **Images:** given a clean checkout, when `make images` runs, then both
  images build and are tagged `sha-<commit>`; the api container answers
  `GET /healthz` 200 and the web container serves the login route over HTTP.
- [ ] **Images (failure):** given the api container starts with a required
  setting absent (e.g. `DATABASE_URL`), when it boots, then it exits non-zero
  naming the missing key in plain text — no stack trace, no secret values —
  and serves no traffic.
- [ ] **Deploy:** given a fresh kind cluster, when `make deploy-check` runs,
  then both images load, the migration Job completes **before** the app
  workloads are applied, every workload (api, web, 5 workers, postgres, redis,
  kafka, clickhouse, minio, kafka-connect) reaches Ready, and the smoke logs in
  through the deployed stack and performs one check-in visible on the board.
- [ ] **Deploy (single replica):** when `make deploy-check` runs, then every
  app workload (api, web, relay, realtime, notifications, payroll, cdc-sink)
  runs exactly one replica — the ADR 0025 single-process constraint — asserted
  from the cluster state, not just the manifests.
- [ ] **Deploy (failure — migration, fresh):** given a fresh kind cluster
  whose migration Job fails, when `make deploy-check` runs, then the app
  workloads are never applied (nothing becomes Ready) and the smoke fails
  naming the Job and its exit state.
- [ ] **Deploy (failure — migration, upgrade):** given the stack deployed at
  SHA n−1, when an upgrade to SHA n attempts a failing migration, then the new
  app workloads are never applied and the n−1 smoke still answers (previous
  deployment keeps serving).
- [ ] **Deploy (failure — missing secret):** given no Secret is applied, when
  the smoke runs, then readiness never passes and the failure names the
  missing configuration key (never its value).
- [ ] **Deploy (failure — wrong secret):** given a Secret with a
  wrong-but-present value (e.g. a bad `DATABASE_URL`), when the smoke runs,
  then the surfaced failure names the key only — never the value, never raw
  driver/DSN error text.
- [ ] **API:** given PostgreSQL is reachable, when `GET /readyz` is called, then
  it answers `200 {"status":"ready"}` with no auth and no data returned.
- [ ] **API (failure):** given PostgreSQL is unreachable or hung, when
  `GET /readyz` is called, then it answers `503` in the house error shape with
  code `dependency_unavailable` within `READYZ_TIMEOUT_SECONDS` (default 2s),
  while `GET /healthz` still answers 200 (liveness stays dependency-free).
- [ ] **CI:** given a push to a PR branch and a merge to `main`, when the
  workflow runs, then `check-api`, `check-web`, `check-e2e` and `check-load`
  all run in the pipeline, and only a fully green `main` run builds and pushes
  both images to GHCR tagged `sha-<commit>`.
- [ ] **CI (failure):** given any gate fails, when the workflow runs, then the
  pipeline fails and no image is pushed — a red gate blocks the pipeline
  exactly as it blocks "done".
- [ ] **Secrets hygiene:** given the repository and both built images, when
  inspected, then no deployed-environment secret values appear in git-tracked
  files, image layers or manifests (the tracked dev-only credentials are never
  reused in a deployed Secret), the workflows reference secrets only via
  `${{ secrets.* }}` env injection and never echo them, and `.env.example`
  lists every configuration key the manifests expect.
- [ ] *(Tenant isolation & denied role: not applicable — this feature adds no
  data surface and no mutating route; access semantics are proven unchanged by
  the untouched `make check` battery.)*

## Decisions (PO, 2026-11-02)

1. **Cluster target: local kind proof, full stack.** The kind cluster runs the
   app processes *and* dev-grade single-node PostgreSQL/Redis/Kafka/
   ClickHouse/MinIO/Kafka Connect; real-cloud deploy is out of scope. Rationale:
   every gate in this repo is a command (`.ai/testing.md`), and a kind proof
   keeps the deploy verification runnable on any machine and in CI with no
   cloud account, credentials or cost.
2. **CI + registry: GitHub Actions + GHCR.** The repo already lives on GitHub
   (`KenKidz/work-pulse`), so the pipeline and the registry need no new
   accounts; images land in GHCR tagged `sha-<commit>`.
3. **Image layout: one Python image + one web image.** Workers select their
   entrypoint by command override on the shared app image. Rationale: ADR
   0002's modular monolith means one deployable API; per-worker images would
   multiply build/version surface for no isolation gain, and an all-in-one
   supervisor image would wreck startup semantics and probes.
4. **CI gate scope: full `make check` in CI.** `check-api`, `check-web`,
   `check-e2e` and `check-load` all run in the pipeline, matching the local
   battery. ADR 0025 flagged k6-on-CI as a risk: if the runner cannot sustain
   it (or Playwright + infra containers push past runner limits), the gate
   placement comes back to the PO as a recorded downgrade — never a silent
   skip.
5. **Cluster secrets: plain Kubernetes Secrets applied out-of-band.** Secret
   values stay outside git (untracked local values / manual `kubectl apply`)
   with a documented key list in the manifests. Rationale: solo maintainer,
   no GitOps controller in scope; sealed-secrets/SOPS and external secret
   managers add key/credential management this phase does not need.

## Plan

Repo research (read-only) confirmed the spec's assumptions: `GET /healthz` is
defined inline in `create_app()` (`apps/api/app/main.py`) and returns
`{"status": "ok", "version": …}` with no dependencies; the house error shape and
`DependencyUnavailableError` (503 `dependency_unavailable`) already live in
`apps/api/app/shared/errors.py` with `register_error_handlers`, so `/readyz`'s
failure path reuses them; the rate limiter (`app/shared/rate_limit.py`)
exempts GETs per ADR 0025 and lists `/healthz` + `/metrics` in `_EXEMPT_PATHS`;
`apps/api/tests/contract/test_openapi_drift.py` asserts `app.openapi() ==
contracts/openapi/openapi.json`, so any new route forces a `make gen-api`
regen; `apps/web/next.config.ts` already sets `outputFileTracingRoot` at the
repo root but has no `output: "standalone"`; the runtime API base URL is
`NEXT_PUBLIC_API_URL` (`packages/api-client/src/mutator/custom-fetch.ts`);
the five workers are `workers/{relay,realtime,notifications,payroll,cdc_sink}/`
with `main.py` entrypoints invoked as
`PYTHONPATH="../..:." python -m workers.<name>.main` from `apps/api`; the
Makefile has no `images`/`deploy-check` targets (check-e2e →
`tests/e2e/full-hop.sh`, check-load → `tests/performance/run.sh`);
`.github/workflows/` contains only a stale generated `playwright.yml` that
runs none of the gates; `docker-compose.yml` pins the infra versions to mirror
in-cluster (postgres:16 with `wal_level=logical`, redis:7-alpine,
apache/kafka:3.9.1 KRaft, quay.io/debezium/connect:3.2.4.Final,
clickhouse/clickhouse-server:24.8-alpine, alpine/minio RELEASE.2025-10-15,
chainguard minio-client init); Alembic runs via `make db-migrate`
(`cd apps/api && uv run alembic upgrade head`, `apps/api/alembic.ini`, 20
revisions); seeds are SQL files piped into psql (`database/postgres/seeds/`,
including the smoke identities); and the smoke flow at API level is
`POST /api/v1/auth/login` (sets `wp_session`) →
`POST /api/v1/attendance/check-ins` → `GET /api/v1/attendance/board`. There is
no `ops/deploy/` and no Dockerfile anywhere yet.

Research gaps this plan resolves explicitly:

1. **Strict boot config check.** `Settings` (`apps/api/app/config.py`,
   pydantic-settings) defaults *every* field — nothing fails boot today, so
   the "missing configuration: `<KEY>`" failure needs new code. The plan adds
   `validate_boot_configuration()` (called at the top of `create_app()` and of
   each worker `main()`) that, when `WORKPULSE_STRICT_CONFIG=1`, checks a
   documented required-key list and exits 1 with plain-text
   `missing configuration: <KEY>` — never values, never a traceback. The flag
   is set only in the images/manifests, so local dev, tests and compose are
   untouched (business rule 8).
2. **Required-key list** (defaults are dev-localhost values, wrong in a
   cluster): `DATABASE_URL`, `REDIS_URL`, `KAFKA_BOOTSTRAP_SERVERS`,
   `CLICKHOUSE_URL`, `STORAGE_ENDPOINT`, `STORAGE_ACCESS_KEY`,
   `STORAGE_SECRET_KEY`, `STORAGE_BUCKET` — one `REQUIRED_BOOT_KEYS` tuple,
   mirrored by `.env.example` and the Secret placeholder key list.
3. **`.dockerignore` naming.** BuildKit resolves per-Dockerfile ignore files as
   `<Dockerfile-name>.dockerignore` next to the Dockerfile (context root
   `.dockerignore` is the fallback). The plan ships
   `apps/api/Dockerfile.dockerignore` + `apps/web/Dockerfile.dockerignore`
   with repo-root build contexts — the spec's Surfaces table intent, with the
   mechanism Docker actually honors.
4. **Seeds in the cluster.** The smoke logs in, so the kind stack needs the
   seed identities: `ops/deploy/seed.sh` pipes `database/postgres/seeds/*.sql`
   into the postgres pod's psql (mirroring `make db-seed`), after the
   migration Job and before the smoke. Seed identities are tracked product
   demo data, not infrastructure secrets — Secret *values* are generated
   out-of-band and never reuse the dev compose credentials.
5. **CI shape.** One workflow `.github/workflows/ci.yml` with the four gate
   jobs and a `build-push` job `needs`-gated on green `main` (cross-job `needs`
   inside one workflow is the only reliable gate). The stale
   `.github/workflows/playwright.yml` scaffold is deleted — it runs Playwright
   without the real `full-hop.sh` gate and would drift from `make check-e2e`.
6. **`/readyz` in the OpenAPI contract.** Included in schema like `/healthz`
   (unlike `/metrics`), so `make gen-api` regenerates
   `contracts/openapi/openapi.json` + `packages/api-client` inside T1 — the
   drift test keeps the surfaces pinned.

### Acceptance criteria → change map

| AC | Satisfied by |
| --- | --- |
| Images | T3 (web standalone) · T4 (two Dockerfiles, `make images`, `sha-<commit>` tags, image smoke) |
| Images (failure) | T2 (strict boot config check) · T4 (image smoke asserts non-zero exit + key name) |
| Deploy | T5 (k8s manifests) · T6 (`make deploy-check` ordering + smoke) |
| Deploy (single replica) | T5 (1-replica Deployments) · T6 (asserted from cluster state) |
| Deploy (failure — migration, fresh) | T6 (deploy tooling stops before app workloads; scenario) |
| Deploy (failure — migration, upgrade) | T6 (upgrade-path scenario) |
| Deploy (failure — missing secret) | T2 (boot check names the key) · T6 (scenario) |
| Deploy (failure — wrong secret) | T1/T2 error hygiene (key-only) · T6 (scenario) |
| API | T1 (`GET /readyz` success path) |
| API (failure) | T1 (503 `dependency_unavailable` within bound; `/healthz` unaffected) |
| CI | T7 (four gates in the pipeline; GHCR push on green `main`) |
| CI (failure) | T7 (`needs` gating; red gate blocks the push) |
| Secrets hygiene | T5 (Secret placeholder + key list) · T6 (out-of-band generated values) · T7 (`${{ secrets.* }}`) · T8 (`.env.example` parity) |

### Ordered tasks

Each task keeps `make check` green on its own; tests ship with the code they
cover. Backend (the one new route) lands first and regenerates its contract in
the same task; the deployment-only targets never join the `make check` battery
(business rule 8) — their proof is `make deploy-check`.

**T1 — Implement `GET /readyz` (contract pin + backend).**
- `apps/api/app/config.py`: add `readyz_timeout_seconds: float = 2.0` (env
  `READYZ_TIMEOUT_SECONDS`).
- `apps/api/app/main.py`: add `GET /readyz` alongside `/healthz` — probe runs
  `SELECT 1` on the async engine bounded by
  `asyncio.wait_for(..., settings.readyz_timeout_seconds)`; success
  `200 {"status": "ready"}`; failure raises `DependencyUnavailableError` →
  house 503 via the existing handlers. No auth, no data, no dependency names.
- `apps/api/app/shared/rate_limit.py`: add `/readyz` to `_EXEMPT_PATHS` next to
  `/healthz` (belt-and-braces; GETs are already exempt per ADR 0025).
- Tests: `apps/api/tests/unit/test_readiness.py` (probe logic: reachable /
  unreachable / timeout → correct outcome, no driver text leaks) and
  `apps/api/tests/integration/test_readyz.py` (real PostgreSQL → 200
  `{"status":"ready"}` unauthenticated; unreachable PostgreSQL → 503
  `dependency_unavailable` answered within the bound while `/healthz` still
  answers 200).
- Verify: `make check-api`; `make gen-api` regenerates
  `contracts/openapi/openapi.json` + `packages/api-client` and the diff is
  committed (`test_openapi_drift.py` enforces parity from then on).

**T2 — Boot configuration validation (`missing configuration: <KEY>`).**
- `apps/api/app/shared/boot.py` (new): `REQUIRED_BOOT_KEYS` (gap 2) +
  `validate_boot_configuration()` — when `WORKPULSE_STRICT_CONFIG=1`, require
  each key to be present in the environment; on failure print plain-text
  `missing configuration: <KEY>` to stderr and `sys.exit(1)`. Runs before any
  connection attempt; never prints values.
- Call it at the top of `create_app()` (`apps/api/app/main.py`) and of each
  `workers/{relay,realtime,notifications,payroll,cdc_sink}/main.py` `main()`.
  Flag unset by default → local dev, tests and compose untouched.
- Tests: `apps/api/tests/unit/test_boot_config.py` (missing key → exit 1 with a
  message naming the key and never the value; all present → no-op; flag unset →
  no-op).
- Verify: `make check-api`.

**T3 — Web standalone output.**
- `apps/web/next.config.ts`: add `output: "standalone"` (keep
  `outputFileTracingRoot` at the repo root — already monorepo-correct).
- Verify: `make check-web` green; one-off `pnpm run build` produces
  `.next/standalone` (documented, not part of the gates).

**T4 — The two images + `make images`.**
- `apps/api/Dockerfile` + `apps/api/Dockerfile.dockerignore`: repo-root build
  context; pinned `python:3.12-slim` base; uv install of `apps/api` deps; copy
  `apps/api/` + `workers/` preserving layout with `PYTHONPATH` matching
  `make dev`'s `../..:.`; non-root runtime user; `ENV
  WORKPULSE_STRICT_CONFIG=1`; default CMD serves `app.main:app` on :8000;
  workers are command overrides (`python -m workers.<name>.main`). No secrets,
  no `.env` files.
- `apps/web/Dockerfile` + `apps/web/Dockerfile.dockerignore`: pinned
  `node:20-alpine` build stage + slim runtime; pnpm install + `next build`
  (standalone); `NEXT_PUBLIC_API_URL` build arg (default
  `http://localhost:8000`); non-root; serves :3000.
- `Makefile`: new `images` target — builds both, tags `sha-<git rev-parse
  --short HEAD>` (+ local `latest` convenience tags). Existing targets
  untouched.
- `ops/deploy/image-smoke.sh`: run the api image → `GET /healthz` 200; run it
  with `DATABASE_URL` withheld → exits non-zero printing
  `missing configuration: DATABASE_URL`; run the web image → `GET /login` 200.
- Verify: `make images && bash ops/deploy/image-smoke.sh` (outside
  `make check`); `make check` still green.

**T5 — Kubernetes manifests (`ops/deploy/k8s/`).**
- `namespace.yaml`; `secret.example.yaml` — placeholder + documented key list
  only (values applied out-of-band, business rule 6).
- Single-node stateful infra mirroring `docker-compose.yml` versions/settings:
  postgres (StatefulSet + PVC, `wal_level=logical`,
  `max_slot_wal_keep_size=1GB`), redis, kafka (KRaft), clickhouse, minio (+ a
  one-shot bucket-init Job mirroring `minio-init`), kafka-connect (Debezium
  image, `workpulse.cdc.connect.*` storage topics) — each with a Service and
  probes where the image supports them.
- App workloads, exactly 1 replica each (business rule 2): Deployments for
  api, web, relay, realtime, notifications, payroll, cdc-sink — the same
  Python image with `command` overrides for the five workers. api: `/healthz`
  liveness + `/readyz` readiness probes; web: `GET /login` probes; workers:
  no HTTP surface in-cluster (`*_METRICS_PORT=0`, ADR 0023) so Ready = the
  container running — the smoke's wait catches crash-loops. Resource
  requests/limits on every workload; env from the Secret + plain settings.
- `migration-job.yaml` — one-shot Job running `alembic upgrade head` on the
  app image; applied and awaited by the deploy tooling before any app workload
  (business rule 4 — the gate is this ordering, not `/readyz`).
- Verify: manifests are proven by `make deploy-check` (T6); `kubectl get`
  assertions live there.

**T6 — Deploy proof: `make deploy-check` + runbook.**
- `ops/deploy/deploy.sh` — the ordering gate: create/reuse the kind cluster →
  `kind load` both images → apply infra → `kubectl wait` infra Ready → apply
  `migration-job.yaml` and `kubectl wait --for=condition=complete` (on
  failure: print the Job name + exit state and **stop before applying any app
  workload**) → `ops/deploy/seed.sh` → register the CDC connector against the
  in-cluster Kafka Connect REST URL (mirroring `make cdc-up`) → apply app
  workloads → `kubectl wait` until every workload (api, web, 5 workers,
  postgres, redis, kafka, clickhouse, minio, kafka-connect) is Ready, printing
  pod status on timeout.
- `ops/deploy/smoke.sh` — `kubectl port-forward` to `svc/workpulse-api` (and
  `svc/workpulse-web` for the `/login` check), then the HTTP flow:
  `POST /api/v1/auth/login` (seeded `worker@workpulse.test`) →
  `POST /api/v1/attendance/check-ins` → `GET /api/v1/attendance/board` asserts
  the row.
- Single-replica assertion read from cluster state (`kubectl … jsonpath` = 1
  per app workload), not just the manifests.
- Failure scenarios (`make deploy-check-scenarios`): fresh-cluster failing
  migration (Job override fails → app manifests never applied; smoke names the
  Job + exit state) · upgrade path (deploy SHA n−1, attempt SHA n with a
  failing migration Job → new Deployments never applied and the n−1 smoke
  still answers) · missing Secret (readiness never passes; failure names the
  key) · wrong Secret (bad `DATABASE_URL` → surfaced failure names the key
  only, never the value/raw driver text).
- `ops/deploy/README.md` — runbook: kind bring-up, Secret application order
  (values generated out-of-band before workloads), migration Job semantics,
  rollback = redeploy the previous SHA's images (schema never downgraded;
  migrations stay backward-compatible with the previous image).
- `Makefile`: `deploy-check`, `deploy-check-scenarios`, `deploy-down`.
- Verify: `make deploy-check` on a Docker+kind machine is the proof; the
  README documents the resource floor.

**T7 — CI pipeline.**
- `.github/workflows/ci.yml`: on PR + push to `main`, four jobs running
  `make check-api`, `make check-web`, `make check-e2e`, `make check-load`
  (each brings up its own infra; k6 installed explicitly — a missing k6 fails
  the gate loudly per ADR 0025; Playwright browsers installed). On push to
  `main` only, a `build-push` job with
  `needs: [check-api, check-web, check-e2e, check-load]` builds both images
  and pushes to GHCR tagged `sha-<short>` (`packages: write`; secrets only via
  `${{ secrets.* }}`, never echoed).
- Delete the stale `.github/workflows/playwright.yml` scaffold.
- Verify: a PR run shows all four gates; any failing gate leaves `build-push`
  un-run (AC CI (failure)).

**T8 — ADR 0026 + docs close-out.**
- `.ai/decisions/0026-deployment-two-image-kind-proof-and-ci.md`: two-image
  layout (ADR 0002), single-replica constraint (ADR 0025), kind-as-proof
  target (no cloud), GHCR + full-gate CI, out-of-band Secrets — plus the
  strict-boot-config flag and in-cluster seed step as consequences.
- `.ai/testing.md` gates table: add a `deploy-check` row (explicitly not part
  of `make check`) and the CI note (the gates now run in CI; a gate that is in
  neither `make check` nor CI protects nothing).
- `.env.example`: add `READYZ_TIMEOUT_SECONDS` + `WORKPULSE_STRICT_CONFIG`;
  verify its key list matches the Secret placeholder key list (AC Secrets
  hygiene).
- `tasks/TODO.md`: move the completed Reliability entry from Now to Done
  (Phase 7 bookkeeping) and tick the Deployment entry; mark the Phase 8 row in
  `.ai/architecture.md`; add `make images` / `make deploy-check` to the
  `AGENTS.md` quickstart.
- Tick every acceptance checkbox in this spec and set `Status: done` once
  T1–T7 are verified.

### Risks & unknowns

- **CI runner capacity** (Playwright browsers + k6 + five infra containers)
  is the biggest risk (ADR 0025 flagged it). If the runner cannot sustain a
  gate, the pipeline fails visibly and the gate's placement returns to the PO
  as a recorded downgrade — never a silent skip.
- **kind resource footprint** (≈11 workloads + persistent volumes) may exceed
  small machines' Docker budgets. Mitigation: the README documents the floor;
  `make deploy-down` reclaims; waits time out with pod status instead of
  hanging.
- **`NEXT_PUBLIC_API_URL` is inlined at web build time** — the browser-facing
  URL is a build arg of the web image; the smoke reaches the API through
  port-forward so the proof stays truthful either way. Documented in the
  runbook.
- **In-cluster CDC connector registration** is new operational surface:
  `ops/cdc/register.sh` must target the in-cluster Connect REST URL before
  cdc_sink is healthy. `deploy.sh` runs it explicitly (mirroring
  `make cdc-up`).
- **Migration-failure fidelity**: the failing migration in the scenarios is a
  deliberately failed Job command, not a real bad revision — it proves the
  rollout-ordering gate, not Alembic behavior. Acceptable for a deploy proof.
- **Image hardening**: bases are pinned by tag; digest-pinning, signing and
  SBOM stay out of scope per the spec.

### Test strategy

| Layer | Where | Covers (failure case) | Gate |
| --- | --- | --- | --- |
| Unit | `apps/api/tests/unit/test_readiness.py` | probe logic — unreachable / timeout (no driver text leaks) | `make check-api` |
| Unit | `apps/api/tests/unit/test_boot_config.py` | missing key → exit 1 naming the key, never the value | `make check-api` |
| Integration | `apps/api/tests/integration/test_readyz.py` | `/readyz` 200 on real PostgreSQL; 503 `dependency_unavailable` within the bound when PostgreSQL is unreachable/hung; `/healthz` unaffected | `make check-api` |
| Contract | `apps/api/tests/contract/test_openapi_drift.py` | OpenAPI parity after `/readyz` (regen committed in T1) | `make check-api` |
| Web | existing vitest battery | unchanged behavior after `output: "standalone"` | `make check-web` |
| E2E / Load | `tests/e2e/`, `tests/performance/` | untouched — regression proof nothing drifted | `make check-e2e` / `make check-load` |
| Deploy | `ops/deploy/smoke.sh` + scenarios | login → check-in → board; migration fresh/upgrade failures, missing/wrong secret, single replica, crash-loop (all workloads Ready) | `make deploy-check` (outside `make check`) |
| CI | `.github/workflows/ci.yml` | all four gates run; a red gate blocks the image push | PR + `main` runs |

### Contract pin (full-stack rule)

The `## API contract` table above pins `GET /readyz` (200 `{"status":"ready"}`
/ 503 house shape `dependency_unavailable`); T1 is the backend against that
pin and regenerates the OpenAPI + `packages/api-client` in the same task
(`test_openapi_drift.py` enforces zero drift from then on). The web work
(T3/T4) is a deployment artifact and consumes no new API shape, so no
frontend task can drift from the contract. `contracts/events/` is untouched —
this feature publishes no events.
