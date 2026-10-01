-- WorkPulse dev seed 003 — Identity (users, sessions-free) + dev worker and
-- dev manager (with the worker reporting to the manager).
--
-- Deterministic and idempotent (INSERT ... ON CONFLICT DO NOTHING): safe to
-- re-run at any time. Synthetic data only — never real people
-- (.ai/security.md). Requires Alembic migration 0004 applied.
--
-- DEV PASSWORD (synthetic, dev-only — never reuse outside local work):
--   workpulse-dev
-- Shared by all four seeded accounts; accounts:
--   admin@workpulse.test   role admin     (id ...0011)
--   hr@workpulse.test      role hr        (id ...0012)
--   worker@workpulse.test  role employee  (id ...0013, linked to employee ...0021)
--   manager@workpulse.test role manager   (id ...0014, linked to employee ...0023)
-- The argon2id hash below is `PasswordHasher()` defaults (t=3, m=64 MiB, p=4);
-- plaintext lives only here and in the .env comment. Change it in real setups.
--
-- The worker user is linked to one synthetic employee (EMP-DEV01, Backend /
-- Software Engineer / Ho Chi Minh Office) with one open employment period so
-- the seeded worker can check in out of the box. The manager user is linked to
-- EMP-DEV02 (Backend / Engineering Manager, same office, own employment
-- period) and the worker reports to them, so a manager-scoped live wall has a
-- report out of the box (realtime-command-center T6). The non-numeric employee
-- codes are ignored by the auto-code scan (EMP-<digits>), so they cannot
-- collide with e2e-created employees — which also keeps this seed's re-run
-- safe.

BEGIN;

INSERT INTO users (id, tenant_id, email, role, employee_id, password_hash, active) VALUES
  ('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'admin@workpulse.test',  'admin',    NULL,                                   '$argon2id$v=19$m=65536,t=3,p=4$1RGL7f7mGKCWo+pkwtX0mA$q9Nqje5ydp1VjIF45o6yCPTulchgwVWwWUYCZz+P6vk', true),
  ('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001', 'hr@workpulse.test',     'hr',       NULL,                                   '$argon2id$v=19$m=65536,t=3,p=4$1RGL7f7mGKCWo+pkwtX0mA$q9Nqje5ydp1VjIF45o6yCPTulchgwVWwWUYCZz+P6vk', true),
  ('00000000-0000-4000-8000-000000000013', '00000000-0000-4000-8000-000000000001', 'worker@workpulse.test',  'employee', '00000000-0000-4000-8000-000000000021', '$argon2id$v=19$m=65536,t=3,p=4$1RGL7f7mGKCWo+pkwtX0mA$q9Nqje5ydp1VjIF45o6yCPTulchgwVWwWUYCZz+P6vk', true),
  ('00000000-0000-4000-8000-000000000014', '00000000-0000-4000-8000-000000000001', 'manager@workpulse.test', 'manager',  '00000000-0000-4000-8000-000000000023', '$argon2id$v=19$m=65536,t=3,p=4$1RGL7f7mGKCWo+pkwtX0mA$q9Nqje5ydp1VjIF45o6yCPTulchgwVWwWUYCZz+P6vk', true)
ON CONFLICT DO NOTHING;

-- The dev worker employee (seed 001 refs: Backend d0003 · Software Engineer
-- e0001 · Ho Chi Minh Office c0001) and the dev manager employee (Backend
-- d0003 · Engineering Manager e0003 · same office).
INSERT INTO employees (id, tenant_id, employee_code, first_name, last_name, email, phone, department_id, position_id, location_id, manager_id, employment_type, status, hired_at, terminated_at) VALUES
  ('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'EMP-DEV01', 'Wendy', 'Worker', 'worker@workpulse.test', NULL, '00000000-0000-4000-8000-0000000d0003', '00000000-0000-4000-8000-0000000e0001', '00000000-0000-4000-8000-0000000c0001', NULL, 'full_time', 'active', DATE '2026-01-05', NULL),
  ('00000000-0000-4000-8000-000000000023', '00000000-0000-4000-8000-000000000001', 'EMP-DEV02', 'Morgan', 'Manager', 'manager@workpulse.test', NULL, '00000000-0000-4000-8000-0000000d0003', '00000000-0000-4000-8000-0000000e0003', '00000000-0000-4000-8000-0000000c0001', NULL, 'full_time', 'active', DATE '2026-01-05', NULL)
ON CONFLICT DO NOTHING;

-- One open employment period per seeded employee — guarded by EXISTS so a
-- skipped employee insert (unique conflict on another key) can never trip the
-- FK; ON CONFLICT keeps re-runs harmless.
INSERT INTO employments (id, tenant_id, employee_id, department_id, position_id, location_id, employment_type, effective_from, effective_to)
SELECT
  '00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
  '00000000-0000-4000-8000-0000000d0003', '00000000-0000-4000-8000-0000000e0001', '00000000-0000-4000-8000-0000000c0001',
  'full_time', DATE '2026-01-05', NULL
WHERE EXISTS (SELECT 1 FROM employees WHERE id = '00000000-0000-4000-8000-000000000021')
ON CONFLICT DO NOTHING;

INSERT INTO employments (id, tenant_id, employee_id, department_id, position_id, location_id, employment_type, effective_from, effective_to)
SELECT
  '00000000-0000-4000-8000-000000000024', '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000023',
  '00000000-0000-4000-8000-0000000d0003', '00000000-0000-4000-8000-0000000e0003', '00000000-0000-4000-8000-0000000c0001',
  'full_time', DATE '2026-01-05', NULL
WHERE EXISTS (SELECT 1 FROM employees WHERE id = '00000000-0000-4000-8000-000000000023')
ON CONFLICT DO NOTHING;

-- Wendy reports to the dev manager so the manager-scoped wall has a report out
-- of the box. Idempotent in effect (the same value is written on every run) and
-- EXISTS-guarded like the employment inserts above: if the manager row was
-- skipped — a conflict on another key — the link is skipped instead of
-- tripping the FK and aborting the seed run.
UPDATE employees
SET manager_id = '00000000-0000-4000-8000-000000000023'
WHERE id = '00000000-0000-4000-8000-000000000021'
  AND EXISTS (SELECT 1 FROM employees WHERE id = '00000000-0000-4000-8000-000000000023');

COMMIT;
