"""Realtime Command Center domain — the live presence wall.

Spec: `.ai/features/realtime-command-center.md`. Pure business logic: the
service composes the attendance board's one projection (`board_detail`) and
consumes presence facts through the `shared/presence.py` ports; Redis and the
WebSocket transport live in `app/infrastructure` and `app/api`. The surface is
read-only — PostgreSQL stays the source of truth, Redis only moves deltas.
"""
