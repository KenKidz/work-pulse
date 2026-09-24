# Events

One directory per domain, one file per event version:

```
events/attendance/
├── attendance-checked-in.v1.json
├── attendance-checked-out.v1.json
└── attendance-late.v1.json
```

Naming: `<domain>-<event>.v<N>.json`, matching `event_type` `<domain>.<event>`
in the envelope.

Example envelope instance:

```json
{
  "event_id": "evt_123",
  "event_type": "attendance.checked_in",
  "version": 1,
  "tenant_id": "company_123",
  "occurred_at": "2026-09-24T09:01:22Z",
  "actor_id": "employee_42",
  "data": {
    "attendance_id": "att_9182",
    "employee_id": "emp_42"
  }
}
```