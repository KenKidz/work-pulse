"""Organizations domain — departments, positions, locations (Phase 1).

Reference data every employee hangs off. Spec: `.ai/features/organizations.md`.
Pure business logic: services depend on repository protocols and the audit
port; Postgres adapters live in `app.infrastructure.postgres`.
"""
