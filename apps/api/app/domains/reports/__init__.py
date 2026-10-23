"""Analytics & Reports domain — a pure consumer of the event stream.

Spec: `.ai/features/analytics-reports.md`; semantics pinned by ADR 0019. The
domain owns the projection fold rules (`folds.py`), the projection/read
protocols (`repository.py`) and the fold-row DTOs (`schemas.py`). It has no
models and emits no events: ClickHouse rows are projections of
`workpulse.events`, written by `workers/reports` and read by the API. Label and
manager-scope lookups are read-only ports (never projection inputs).
"""
