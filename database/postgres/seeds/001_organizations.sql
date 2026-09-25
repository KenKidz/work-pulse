-- WorkPulse dev seed 001 — Organizations (departments, positions, locations).
--
-- Deterministic and idempotent (INSERT ... ON CONFLICT DO NOTHING): safe to
-- re-run at any time. Synthetic data only — never real people
-- (.ai/security.md). Requires Alembic migration 0001 applied.
--
-- Fixed ids: dev tenant 00000000-0000-4000-8000-000000000001,
-- dev actor  00000000-0000-4000-8000-000000000002 (matches .env.example /
-- apps/web dev-identity defaults).

BEGIN;

-- Departments (nested: Acme Corp → Engineering → Backend/Frontend, …)
INSERT INTO departments (id, tenant_id, name, parent_department_id) VALUES
  ('00000000-0000-4000-8000-0000000d0001', '00000000-0000-4000-8000-000000000001', 'Acme Corp',   NULL),
  ('00000000-0000-4000-8000-0000000d0002', '00000000-0000-4000-8000-000000000001', 'Engineering', '00000000-0000-4000-8000-0000000d0001'),
  ('00000000-0000-4000-8000-0000000d0003', '00000000-0000-4000-8000-000000000001', 'Backend',     '00000000-0000-4000-8000-0000000d0002'),
  ('00000000-0000-4000-8000-0000000d0004', '00000000-0000-4000-8000-000000000001', 'Frontend',    '00000000-0000-4000-8000-0000000d0002'),
  ('00000000-0000-4000-8000-0000000d0005', '00000000-0000-4000-8000-000000000001', 'People Ops',  '00000000-0000-4000-8000-0000000d0001'),
  ('00000000-0000-4000-8000-0000000d0006', '00000000-0000-4000-8000-000000000001', 'Operations',  '00000000-0000-4000-8000-0000000d0001')
ON CONFLICT DO NOTHING;

-- Positions (tenant-global — PO Decision #2)
INSERT INTO positions (id, tenant_id, title, description) VALUES
  ('00000000-0000-4000-8000-0000000e0001', '00000000-0000-4000-8000-000000000001', 'Software Engineer',       'Builds and ships product software'),
  ('00000000-0000-4000-8000-0000000e0002', '00000000-0000-4000-8000-000000000001', 'Senior Software Engineer','Owns a technical area end to end'),
  ('00000000-0000-4000-8000-0000000e0003', '00000000-0000-4000-8000-000000000001', 'Engineering Manager',     'Leads an engineering team'),
  ('00000000-0000-4000-8000-0000000e0004', '00000000-0000-4000-8000-000000000001', 'HR Specialist',           'People operations and records'),
  ('00000000-0000-4000-8000-0000000e0005', '00000000-0000-4000-8000-000000000001', 'Office Administrator',    'Keeps a location running')
ON CONFLICT DO NOTHING;

-- Locations (one in Asia/Ho_Chi_Minh — attendance lateness is judged in it)
INSERT INTO locations (id, tenant_id, name, address, timezone) VALUES
  ('00000000-0000-4000-8000-0000000c0001', '00000000-0000-4000-8000-000000000001', 'Ho Chi Minh Office', '1 Nguyen Hue Blvd, District 1, Ho Chi Minh City', 'Asia/Ho_Chi_Minh'),
  ('00000000-0000-4000-8000-0000000c0002', '00000000-0000-4000-8000-000000000001', 'Berlin Office',      '1 Musterstrasse, 10115 Berlin',                     'Europe/Berlin'),
  ('00000000-0000-4000-8000-0000000c0003', '00000000-0000-4000-8000-000000000001', 'Remote',             NULL,                                                 'UTC')
ON CONFLICT DO NOTHING;

COMMIT;
