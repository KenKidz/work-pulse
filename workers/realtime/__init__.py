"""Realtime consumer — projects `workpulse.events` onto the presence bus (ADR 0013).

One instance in v1. Layers: `mapping` (pure envelope → `PresenceFact`),
`consumer` (the fetch → map → publish → commit loop), `main` (settings,
signals).
"""
