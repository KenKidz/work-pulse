"""Background workers (Phase 3+).

Workers share the `apps/api` uv environment and its `app.*` modules instead of
duplicating settings or repositories (ADR 0012 decision 10 — revisit as its own
ADR when a second worker arrives). Each worker is a subpackage with a `main.py`
entry point run from `apps/api` as
`PYTHONPATH="../..:." python -m workers.<name>.main` — see `make dev-relay`.
"""
