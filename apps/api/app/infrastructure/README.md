# Infrastructure

Adapters that talk to the outside world. Domains never import these directly —
they go through repository/service interfaces, and this layer is wired at the
edges (routers, dependencies, workers).

| Package | Arrives in |
| --- | --- |
| `postgres/` | Phase 1 — with the first persistence feature (engine, sessions, repositories) |
| `redis/` | Phase 2 — realtime hot state |
| `kafka/` | Phase 3 — producer/consumer plumbing (outbox) |
| `clickhouse/` | Phase 4 — analytical writes |
| `storage/` | Phase 4+ — employee documents (MinIO/S3, ADR 0022) |
| `email/` | when a spec needs it |