"""Analytics & Reports projection worker — `workpulse.events` → ClickHouse.

One instance in v1 (ADR 0019 decision 6). Layers: `mapping` (pure envelope →
fold command), `consumer` (the fetch → parse → map → fold → write → commit
loop), `main` (settings, ClickHouse client, schema, signals). The fold rules
themselves live in `app.domains.reports.folds` and the ClickHouse writer in
`app.infrastructure.clickhouse.projection`, so this package stays a thin
transport adapter around the domain seam the API shares.
"""
