"""Analytics & Reports domain — the read side of the analytical projection.

Spec: `.ai/features/analytics-reports.md`; semantics pinned by ADR 0019 and
amended by ADR 0024. The domain owns the report shapes and HTTP contract
(`schemas.py`), read-side as-of attribution (`attribution.py`), the read
protocol (`repository.py`), the access policy (`policies.py`), the two
read-only lookup ports (`ports.py`) and the query orchestration/metric math
(`service.py`). It has no models and emits no events: ClickHouse rows are a
projection of the PostgreSQL log, landed by `workers/cdc_sink` and read by the
API. Label and manager-scope lookups are read-only ports (never projection
inputs).
"""
