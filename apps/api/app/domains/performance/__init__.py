"""Performance domain — goals, reviews, peer feedback and skills.

Spec: `.ai/features/performance.md`. Pure business logic: services depend on
repository protocols, the audit port and the outbox port; Postgres adapters
live in `app.infrastructure.postgres`. `events.py` ships the payload builders
consumed by the contract tests and, from T5 on, by the service through the
transactional outbox (ADR 0012).
"""
