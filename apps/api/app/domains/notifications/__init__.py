"""Notifications domain — delivery (worker) plus the own-only inbox (Phase 3).

Specs: `.ai/features/notifications.md` (delivery) and
`.ai/features/notification-inbox.md` (read side); semantics pinned by ADR 0015
and ADR 0016. The worker maps, resolves and renders through the
`RecipientDirectory` port, then `NotificationService` persists one notification
per `(event_id, recipient)` and drives the email and Slack deliveries to a
terminal state. `NotificationInboxService` exposes the person-addressed inbox
over HTTP: list, unread count, mark-read and mark-all-read, own-only for every
role. Read state is the one user-owned overlay on the projection; the domain
emits no event and the inbox mutations are not audited. Postgres adapters live
in `app.infrastructure.postgres`, provider adapters in
`app.infrastructure.notifications`.
"""
