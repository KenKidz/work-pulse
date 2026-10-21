"""Notification consumer — turns `workpulse.events` into person-addressed deliveries.

One instance in v1. Layers: `mapping` (pure envelope → `NotificationIntent`),
`templates` (pure intent → stored subject/body), `consumer` (the
fetch → parse → map → resolve → render → deliver → commit loop), `main`
(settings, provider adapters, sessions, signals). Shapes and semantics are
pinned by ADR 0015; the loop mirrors `workers/realtime/` (ADR 0013 decision 5).
"""
