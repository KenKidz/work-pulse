-- WorkPulse dev seed 002 — Attendance (shift reference data).
--
-- Deterministic and idempotent (INSERT ... ON CONFLICT DO NOTHING): safe to
-- re-run at any time. Synthetic data only — never real people
-- (.ai/security.md). Requires Alembic migration 0003 applied.
--
-- One tenant-default shift (09:00–18:00, grace 10) and one Ho Chi Minh Office
-- scoped shift (08:30–17:30, grace 10) so location-scoped lateness is
-- demonstrable: HCM employees are judged in Asia/Ho_Chi_Minh against
-- 08:30 + 10, everyone else against the tenant default. Employee rows: seed
-- 003 adds one dev worker (a login-able account); e2e and manual testing
-- create their own.

BEGIN;

INSERT INTO shifts (id, tenant_id, name, start_time, end_time, grace_minutes, location_id) VALUES
  ('00000000-0000-4000-8000-0000000f0001', '00000000-0000-4000-8000-000000000001', 'Default (09:00-18:00)',            '09:00', '18:00', 10, NULL),
  ('00000000-0000-4000-8000-0000000f0002', '00000000-0000-4000-8000-000000000001', 'Ho Chi Minh Office (08:30-17:30)', '08:30', '17:30', 10, '00000000-0000-4000-8000-0000000c0001')
ON CONFLICT DO NOTHING;

COMMIT;
