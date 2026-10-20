"""Notifications domain — the event stream's delivery capability (Phase 3).

Spec: `.ai/features/notifications.md`; semantics pinned by ADR 0015. Pure
bookkeeping around the `workpulse.events` consumer: the worker maps, resolves
and renders (through the `RecipientDirectory` port), then `NotificationService`
persists one notification per `(event_id, recipient)` and drives the email and
Slack deliveries to a terminal state. There is no HTTP surface, no user-facing
capability and no emitted event; Postgres adapters live in
`app.infrastructure.postgres`, provider adapters in
`app.infrastructure.notifications`.
"""
