-- WorkPulse dev seed 003 — Identity (users, sessions-free) + one dev worker.
--
-- Deterministic and idempotent (INSERT ... ON CONFLICT DO NOTHING): safe to
-- re-run at any time. Synthetic data only — never real people
-- (.ai/security.md). Requires Alembic migration 0004 applied.
--
-- DEV PASSWORD (synthetic, dev-only — never reuse outside local work):
--   workpulse-dev
-- Shared by all three seeded accounts; accounts:
--   admin@workpulse.test  role admin     (id ...0011)
--   hr@workpulse.test     role hr        (id ...0012)
--   worker@workpulse.test role employee  (id ...0013, linked to employee ...0021)
-- The argon2id hash below is `PasswordHasher()` defaults (t=3, m=64 MiB, p=4);
-- plaintext lives only here and in the .env comment. Change it in real setups.
--
-- The worker user is linked to one synthetic employee (EMP-DEV01, Backend /
-- Software Engineer / Ho Chi Minh Office) with one open employment period so
-- the seeded worker can check in out of the box. The non-numeric employee code
-- is ignored by the auto-code scan (EMP-<digits>), so it cannot collide with
-- e2e-created employees — which also keeps this seed's re-run safe.

BEGIN;

INSERT INTO users (id, tenant_id, email, role, employee_id, password_hash, active) VALUES
  ('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'admin@workpulse.test',  'admin',    NULL,                                   '$argon2id$v=19$m=65536,t=3,p=4$1RGL7f7mGKCWo+pkwtX0mA$q9Nqje5ydp1VjIF45o6yCPTulchgwVWwWUYCZz+P6vk', true),
  ('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001', 'hr@workpulse.test',     'hr',       NULL,                                   '$argon2id$v=19$m=65536,t=3,p=4$1RGL7f7mGKCWo+pkwtX0mA$q9Nqje5ydp1VjIF45o6yCPTulchgwVWwWUYCZz+P6vk', true),
  ('00000000-0000-4000-8000-000000000013', '00000000-0000-4000-8000-000000000001', 'worker@workpulse.test', 'employee', '00000000-0000-4000-8000-000000000021', '$argon2id$v=19$m=65536,t=3,p=4$1RGL7f7mGKCWo+pkwtX0mA$q9Nqje5ydp1VjIF45o6yCPTulchgwVWwWUYCZz+P6vk', true)
ON CONFLICT DO NOTHING;

-- The dev worker employee (seed 001 refs: Backend d0003 · Software Engineer
-- e0001 · Ho Chi Minh Office c0001).
INSERT INTO employees (id, tenant_id, employee_code, first_name, last_name, email, phone, department_id, position_id, location_id, manager_id, employment_type, status, hired_at, terminated_at) VALUES
  ('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'EMP-DEV01', 'Wendy', 'Worker', 'worker@workpulse.test', NULL, '00000000-0000-4000-8000-0000000d0003', '00000000-0000-4000-8000-0000000e0001', '00000000-0000-4000-8000-0000000c0001', NULL, 'full_time', 'active', DATE '2026-01-05', NULL)
ON CONFLICT DO NOTHING;

-- One open employment period — guarded by EXISTS so a skipped employee insert
-- (unique conflict on another key) can never trip the FK; ON CONFLICT keeps
-- re-runs harmless.
INSERT INTO employments (id, tenant_id, employee_id, department_id, position_id, location_id, employment_type, effective_from, effective_to)
SELECT
  '00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
  '00000000-0000-4000-8000-0000000d0003', '00000000-0000-4000-8000-0000000e0001', '00000000-0000-4000-8000-0000000c0001',
  'full_time', DATE '2026-01-05', NULL
WHERE EXISTS (SELECT 1 FROM employees WHERE id = '00000000-0000-4000-8000-000000000021')
ON CONFLICT DO NOTHING;

COMMIT;
