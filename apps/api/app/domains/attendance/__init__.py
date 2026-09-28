"""Attendance domain — check-in/out records, lateness and the workforce board.

Spec: `.ai/features/attendance.md`. Pure business logic: services depend on
repository protocols, the audit port and the attendance/location lookup ports;
Postgres adapters live in `app.infrastructure.postgres`. Publishing events is
Phase 3 — `events.py` ships the payload builders consumed by the contract
tests until the outbox lands.
"""
