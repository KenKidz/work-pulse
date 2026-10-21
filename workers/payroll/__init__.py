"""Payroll calculation worker — `payroll_runs` jobs → calculated results.

Layers: `runner` (the poll → claim/calculate loop) and `main` (settings,
engine, signals). The worker runs inside the `apps/api` uv environment
(`make dev-payroll`), exactly like the outbox relay (ADR 0012) and the other
Phase 3 consumers; it shares their settings/`.env`.
"""
