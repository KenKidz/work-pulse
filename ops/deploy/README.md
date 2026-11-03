# Deploy runbook — kind proof (deployment T6)

The deployment proof runs the full WorkPulse stack in a local
[kind](https://kind.sigs.k8s.io/) cluster: the API, the web app and all five
workers, plus single-node PostgreSQL, Redis, Kafka, ClickHouse, MinIO and
Debezium Kafka Connect. It is a **proof**, not a production topology: every app
process runs exactly one replica because circuit-breaker state is per process
(ADR 0025), and the infra is dev-grade with persistent volumes.

`make deploy-check` is the command. There is no console click-through.

## Prerequisites

- Docker with at least **4 CPUs / 8 GB RAM** free for the kind node, plus
  roughly 10 GB of disk for the node image and persistent volumes.
- `kind`, `kubectl`, `curl` and `jq` on `PATH`.
- `make images` once (or before each new commit): it builds
  `workpulse-api:sha-<commit>` and `workpulse-web:sha-<commit>`.

`make deploy-check` creates or reuses the `workpulse` kind cluster and loads
both images with `kind load`. It never builds images itself, so a stale
`sha-<commit>` tag is a loud "image not found — run `make images` first".

## What the proof does (ordering gate)

1. Applies the namespace, the out-of-band Secret and the ConfigMap.
2. Applies `ops/deploy/k8s/infra/` and waits for postgres, redis, kafka,
   clickhouse, minio and kafka-connect to be Ready and for the `minio-init`
   bucket Job to complete.
3. Applies the migration Job (`alembic upgrade head`) and waits for it to
   **complete**. A failed migration stops here: no app workload is applied.
4. Seeds the tracked demo identities (`ops/deploy/seed.sh`, mirroring
   `make db-seed`).
5. Registers the Debezium connector against the in-cluster Kafka Connect REST
   API (port-forwarded), then stops the forward.
6. Applies `ops/deploy/k8s/apps/` (api, web, relay, realtime, notifications,
   payroll, cdc-sink), waits for every workload Ready, and asserts from cluster
   state that each app Deployment has exactly one desired/ready replica.
7. Runs `ops/deploy/smoke.sh`: port-forwards the API and web Services, checks
   `/healthz`, `/readyz` and `/login`, logs in as the seeded worker, closes any
   open record from a previous run, checks in, then reads the board as the
   seeded HR user and asserts the check-in is the newest activity.

The migration is the gate because of **apply ordering**, not because `/readyz`
knows about migrations. On an upgrade, a failing migration leaves the previous
Deployments untouched and serving.

## Secrets

All configuration keeps the existing env-var model. The manifests read a
Kubernetes Secret named `workpulse-config` in the `workpulse` namespace.
`ops/deploy/k8s/secret.example.yaml` documents the keys and ships placeholders;
**never apply it**.

On the first `make deploy-check`, the tooling generates
`ops/deploy/values.env` (untracked, mode 600) with URL-safe random values and
creates the Secret from it. The file is the source of truth for the cluster's
credentials:

```dotenv
DATABASE_URL=postgresql+asyncpg://workpulse:<generated>@postgres:5432/workpulse
REDIS_URL=redis://redis:6379/0
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
CLICKHOUSE_URL=http://workpulse:<generated>@clickhouse:8123/workpulse
STORAGE_ENDPOINT=http://minio:9000
STORAGE_ACCESS_KEY=<generated>
STORAGE_SECRET_KEY=<generated>
STORAGE_BUCKET=documents
POSTGRES_PASSWORD=<generated>
CLICKHOUSE_PASSWORD=<generated>
```

- To supply your own values, edit or replace the file **before the first
  deploy**. Passwords are embedded in URLs, so use URL-safe values (or
  percent-encode the URL forms).
- Reuse the same file across redeploys of the same cluster: it must match the
  password PostgreSQL and ClickHouse initialized their volumes with.
- The tracked dev credentials in `docker-compose.yml` / `.env.example` are
  dev-only and are never reused in a deployed Secret.
- The tooling never prints values. Failure diagnostics name keys only.
- The CDC connector receives `POSTGRES_PASSWORD` as
  `CDC_POSTGRES_PASSWORD`; `ops/cdc/register.sh` applies it to the connector
  config without changing the tracked dev default.

## Failure scenarios

```bash
make deploy-check-scenarios
```

This runs four scenarios against the same kind cluster:

1. **Upgrade, failing migration** — deploys a synthetic previous tag
   (`sha-<commit>-prev`), then attempts the current tag with
   `ops/deploy/k8s/scenarios/migration-fail-job.yaml`. Asserts the Job failure
   and exit state are named, every app Deployment still runs the previous
   image, and the previous stack still passes the smoke.
2. **Missing Secret** — deletes `workpulse-config` and restarts the API. The
   smoke never reaches readiness and its output contains the strict-boot line
   `missing configuration: DATABASE_URL`; generated values never appear.
3. **Wrong Secret** — applies a copy of the values with a wrong-but-present
   `DATABASE_URL`, restarts the API, and asserts the smoke names `DATABASE_URL`
   only: boot succeeds (no `missing configuration`), the bad value never
   appears, and no driver/DSN text is printed.
4. **Fresh cluster, failing migration** — deletes the kind cluster, runs the
   deploy proof with the failing migration Job, and asserts the migration
   failure is reported and no app Deployment exists.

Scenario 4 deletes the cluster, so it runs last and leaves the cluster holding
infra plus the failed Job. `make deploy-down` reclaims it.

## Manual inspection

```bash
kubectl --context kind-workpulse -n workpulse get pods
kubectl --context kind-workpulse -n workpulse get deploy,sts,job
kubectl --context kind-workpulse -n workpulse logs deployment/api --tail=100
```

To browse the deployed web app, forward both Services on their image defaults:

```bash
kubectl --context kind-workpulse -n workpulse port-forward svc/workpulse-api 8000:8000
kubectl --context kind-workpulse -n workpulse port-forward svc/workpulse-web 3000:3000
# http://localhost:3000/login  (seeded worker: worker@workpulse.test / workpulse-dev)
```

`NEXT_PUBLIC_API_URL` is inlined into the web image at build time and defaults
to `http://localhost:8000`, which matches the API forward above. Rebuild the
web image with `--build-arg NEXT_PUBLIC_API_URL=...` to change it.

## Rollback

Migrations are append-only and backward-compatible with the previously
deployed image, so rollback is **image rollback only** — never a schema
downgrade:

```bash
# The previous image must exist locally (or be pulled from GHCR).
IMAGE_TAG=sha-<previous> DEPLOY_SKIP_MIGRATION=1 bash ops/deploy/deploy.sh
```

`DEPLOY_SKIP_MIGRATION=1` is **rollback-only**: after a successful forward
migration the database is stamped with a revision the previous image's Alembic
scripts do not know, so re-running that image's `alembic upgrade head` would
fail with "Can't locate revision". The old image is safe against the newer
schema because migrations are append-only and backward-compatible; the flag
skips only the migration gate, never the infra or app readiness waits. Never
set it for a forward deploy. If a migration fails, the previous Deployments
are never replaced.

## Teardown

```bash
make deploy-down
```

Deletes the kind cluster and all persistent volumes. `ops/deploy/values.env`
is kept (it is harmless without the cluster); delete it too when you want a
fresh set of generated credentials.

## Scope notes

- No ingress, TLS, DNS, cloud clusters, autoscaling, HA or in-cluster
  observability — see `.ai/features/deployment.md` (Out of scope).
- Worker metrics listeners are disabled in-cluster (`*_METRICS_PORT=0`,
  ADR 0023); a worker is Ready when its container is running, and the deploy
  proof catches crash-loops by waiting for every workload.
- The web app is checked at `/login`; the smoke's product flow is the API path
  through the port-forward.
- Scenario 1's previous tag is synthetic (`sha-<commit>-prev`) and scenario 4's
  failing Job is deliberate. They prove the ordering gate, not Alembic or
  image-archaeology behavior.
