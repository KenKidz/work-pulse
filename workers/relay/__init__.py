"""Outbox relay — publishes committed `event_outbox` rows to Kafka (ADR 0012).

One instance in v1. Layers: `relay` (the poll → publish → mark loop),
`publisher` (the `aiokafka` adapter), `main` (settings, engine, signals).
"""
