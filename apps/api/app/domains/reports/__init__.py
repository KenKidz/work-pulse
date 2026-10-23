"""Analytics & Reports domain — a pure consumer of the event stream.

Spec: `.ai/features/analytics-reports.md`; semantics pinned by ADR 0019. The
domain owns the projection fold rules (`folds.py`), the projection/read
protocols (`repository.py`), the fold-row and HTTP contract types
(`schemas.py`), the access policy (`policies.py`), the two read-only lookup
ports (`ports.py`) and the query orchestration/metric math (`service.py`). It
has no models and emits no events: ClickHouse rows are projections of
`workpulse.events`, written by `workers/reports` and read by the API. Label and
manager-scope lookups are read-only ports (never projection inputs).
"""
