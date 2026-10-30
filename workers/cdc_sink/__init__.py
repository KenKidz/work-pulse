"""CDC projection sink — `workpulse.cdc.*` → ClickHouse (ADR 0024, T4).

One instance in v1. Layers: `mapping` (pure Debezium envelope → typed rows),
`consumer` (the fetch → parse → map → upsert → commit loop), `main` (settings,
ClickHouse client, schema, signals). The ClickHouse adapter lives in
`app.infrastructure.clickhouse.cdc_projection`, so this package stays a thin
transport adapter and never imports a database driver directly.
"""
