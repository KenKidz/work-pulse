"""Employees domain — the workforce aggregate (Phase 1).

Spec: `.ai/features/employees.md`. Pure business logic: services depend on
repository protocols, the audit port and the Organizations services (reference
checks); Postgres adapters live in `app.infrastructure.postgres`. Publishing
events is Phase 3 — `events.py` ships the payload builders consumed by the
contract tests until the outbox lands.
"""
