"""Recruitment domain — jobs, candidates, interviews and the hire seam.

Spec: `.ai/features/recruitment.md`. Pure business logic: services depend on
repository protocols, the audit port, the outbox port and an Employees hire
port; Postgres adapters live in `app.infrastructure.postgres`. `events.py`
ships the payload builders consumed by the contract tests and, from T2 on, by
the service through the transactional outbox (ADR 0012).
"""
