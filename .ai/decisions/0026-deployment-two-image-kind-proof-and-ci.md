# 26. Deployment: two images, kind proof, full-gate CI and out-of-band Secrets

- **Status:** Accepted
- **Date:** 2026-11-03

## Context

Phase 8 (`.ai/features/deployment.md`) makes WorkPulse shippable as software.
Until now every surface ran as a native host process (`uvicorn`, `next dev`,
`python -m workers.<name>.main`) over docker-compose infrastructure: there were
no deployable artifacts, no cluster manifests and no pipeline, so a "release"
was a `git pull` on one developer machine. The phase must produce versioned
container images, a Kubernetes deployment proven end to end, and CI that runs
every gate before anything ships.

The constraints that shape the decision are already pinned by earlier ADRs:
ADR 0002 makes this a modular monolith with one deployable API; ADR 0025 puts
circuit-breaker state in process memory, so replicas cannot safely diverge;
ADR 0023 binds worker metrics listeners to loopback (in-cluster scraping would
need a separate bind-address decision); ADR 0024 runs Kafka Connect/Debezium
alongside the applications as a third-party process. The spec's PO decisions
(2026-11-02) close the open questions: a local kind proof over the full stack,
GitHub Actions + GHCR, one Python image + one web image, all four gates in CI,
and plain Kubernetes Secrets applied out of band.

Alternatives considered:

- **One image per process (API + five workers).** Six Python images would
  multiply build, tag and vulnerability surface for zero isolation gain in a
  modular monolith (ADR 0002). Rejected.
- **An all-in-one supervisor image running the API and the workers.** One
  rollout unit, but startup, readiness and crash semantics collapse into a
  supervisor, and one crashing worker would take the API with it. Rejected.
- **A real cloud cluster / managed services.** The proof would need an
  account, credentials and spend to verify a phase gate, and every gate in this
  repo is a command any maintainer can run. Rejected; cloud deployment stays
  out of scope.
- **GitOps (Argo/Flux) or sealed-secrets/SOPS/external secret managers.**
  Valuable at team scale, but they add a controller and a key-management
  problem this phase does not need. Rejected; Secrets stay plain and
  out-of-band.
- **CI running only a fast subset of the gates.** A gate that is skipped in CI
  protects nothing, and "green enough" makes the local battery advisory.
  Rejected; all four gates run on every PR and `main` push.
- **Schema downgrade as part of rollback.** Alembic downgrades are rarely
  exercised and can lose data. Rejected; migrations stay append-only and
  backward-compatible, so rollback is image rollback only.

## Decision

1. **Exactly two images.** `workpulse-api` holds `apps/api` + `workers/`, with
   the API as the default command; each worker is the same image with a
   `python -m workers.<name>.main` command override. `workpulse-web` is the
   Next.js app with `output: "standalone"`. Both run as non-root, pin their
   base image tags, carry no secrets or `.env` files, and are built and tagged
   `sha-<short>` by `make images`.

2. **Single replica per app process is a hard constraint.** Because breaker
   state is in memory (ADR 0025 §5), api, web and the five workers each deploy
   exactly one replica, and the deploy proof asserts desired/ready replicas
   from cluster state. Infrastructure is single-node and dev-grade for the same
   proof scope. Scaling any app process beyond one, or running multi-node HA
   infrastructure, requires shared breaker state and a new ADR.

3. **The cluster target is a local kind proof over the full stack.**
   `ops/deploy/k8s/` deploys the app workloads plus single-node PostgreSQL,
   Redis, Kafka, ClickHouse, MinIO and Kafka Connect (Debezium), with
   persistent volumes, Services, probes and resource requests/limits.
   `make deploy-check` (`ops/deploy/deploy.sh`) is the proof: it creates or
   reuses the kind cluster, loads both images, applies and awaits infra, runs
   the migration Job, seeds demo identities, registers the CDC connector,
   applies the app workloads, waits for every workload to be Ready, asserts
   single replicas and runs the HTTP smoke. There is no console click-through.

4. **The migration gate is apply ordering, not readiness.** Alembic runs as a
   one-shot Job that the deploy tooling applies and awaits to completion before
   applying any app workload. On a fresh cluster no app pod starts until
   migrations succeed; on an upgrade a failing migration means the new
   Deployments are never applied and the previous deployment keeps serving.
   `GET /readyz` reports PostgreSQL reachability only and knows nothing about
   migration state. Migrations remain append-only and backward-compatible with
   the previously deployed image, so rollback is image rollback only
   (`DEPLOY_SKIP_MIGRATION=1` is documented as rollback-only) — never a schema
   downgrade.

5. **CI runs the full gate battery.** `.github/workflows/ci.yml` runs
   `make check-api`, `make check-web`, `make check-e2e` and `make check-load`
   on every PR and `main` push. Only a fully green `main` run executes the
   `build-push` job, which builds both images and pushes them to GHCR tagged
   `sha-<short>`; a red or skipped gate leaves the registry untouched. A
   missing k6 binary fails the load gate loudly rather than skipping it. The
   stale generated `playwright.yml` scaffold is deleted so CI cannot drift from
   `make check-e2e`.

6. **Secrets reach the cluster out of band.** Configuration keeps the existing
   environment-variable `Settings` model, populated in-cluster from the
   `workpulse-config` Secret. `ops/deploy/k8s/secret.example.yaml` tracks only
   placeholders and the documented key list; real values come from an
   untracked `ops/deploy/values.env` generated by the tooling (mode 600) or a
   manual `kubectl apply`. The dev-only credentials in `docker-compose.yml` and
   `.env.example` are never reused in a deployed environment. Workflows
   reference secrets only through `${{ secrets.* }}` and never echo them; the
   tooling and failure diagnostics name keys, never values.

7. **Liveness is not readiness.** `GET /healthz` stays dependency-free liveness.
   The new `GET /readyz` runs `SELECT 1` bounded by
   `READYZ_TIMEOUT_SECONDS` (default 2s) and returns `200 {"status":"ready"}` or
   the house `503 dependency_unavailable` shape. Both are unauthenticated for
   kubelet, expose no dependency details or data, and are GETs, so ADR 0025's
   rate limiter leaves them unthrottled; `/readyz` is also listed in
   `_EXEMPT_PATHS` as belt-and-braces.

8. **Deployed processes validate boot configuration strictly.** Every
   `Settings` field has a dev-localhost default, which is silently wrong in a
   cluster. The images set `WORKPULSE_STRICT_CONFIG=1` (and the ConfigMap
   repeats it); `validate_boot_configuration()` then requires
   `REQUIRED_BOOT_KEYS` to be present and non-blank, exiting 1 with a
   plain-text `missing configuration: <KEY>` before any connection attempt —
   no value, no traceback. The flag is absent in local dev, tests and compose,
   so business rule 8 holds.

9. **The proof seeds tracked demo identities.** The smoke logs in through the
   deployed stack, so `ops/deploy/seed.sh` pipes the same tracked SQL seeds
   used by `make db-seed` into the postgres pod after the migration Job and
   before the smoke. Seed identities are product demo data, not
   infrastructure secrets; generated Secret values never reuse the dev
   compose credentials.

10. **Deployment does not disturb local development.** `make dev` and the
    compose-based gates are unchanged. Deployment adds images, `ops/deploy/`
    files, Makefile targets and `.github/` workflows only; the observability
    stack stays the local `ops/observability` tool it is today.

## Consequences

- A Python change produces one image and a web change one image; the workers
  version in lockstep with the API, which is the honest consequence of the
  modular monolith. Worker rollouts are Deployments with command overrides.
- Every app workload is single-replica by construction. There is no
  autoscaling and no shared breaker state; a future scale-out requirement must
  land the shared state first (new ADR).
- `make deploy-check` is deliberately outside `make check`: it needs Docker,
  `kind`, `kubectl` and roughly 4 CPU / 8 GB RAM plus 10 GB disk, and it is the
  release/deployment proof rather than a per-change gate. The runbook
  (`ops/deploy/README.md`) documents the floor, the Secret order, the migration
  semantics and the rollback command; `make deploy-check-scenarios` proves the
  failure paths (fresh and upgrade migration failures, missing and wrong
  Secret).
- CI proves the four functional gates but not the kind proof, for the same
  runner-capacity reason. `deploy-check` stays a documented, manual,
  release-time gate rather than a per-change one, and is never silently
  assumed.
- `/readyz` is part of the OpenAPI contract, so `make gen-api` regenerated
  `contracts/openapi/openapi.json` and `packages/api-client` and the drift test
  pins it thereafter.
- Failure behavior is explicit: a failed migration leaves the previous
  deployment serving; a missing Secret exits naming the key; a wrong-but-
  present Secret boots and fails at first use naming the key only; an unloaded
  image surfaces `ImagePullBackOff` via pod status rather than a hung proof.
- Rotating a Secret requires re-applying it and restarting the affected pods;
  there is no external secret manager or automatic reload.
- Image hardening stops at pinned tags and non-root runtime: digest pinning,
  signing (cosign), SBOMs and a base-image CVE policy remain out of scope.
- `NEXT_PUBLIC_API_URL` is inlined into the web image at build time, so changing
  the browser-facing API URL requires rebuilding the image; the runbook says so.
- Teardown is `make deploy-down`, which deletes the kind cluster and its
  persistent volumes; the untracked values file is kept (and can be deleted to
  rotate credentials).
