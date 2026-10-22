"""Overtime domain — requests, two-step decisions and the payroll seam facts.

Spec: `.ai/features/overtime.md`. Pure business logic: services depend on
repository protocols, the audit port and the outbox port; Postgres adapters
live in `app.infrastructure.postgres`. `events.py` ships the payload builders
consumed by the contract tests and, from T4 on, by the service through the
transactional outbox (ADR 0012).
"""
