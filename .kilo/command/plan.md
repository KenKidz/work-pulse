---
description: Turn an approved spec into an ordered implementation plan
agent: planner
---

Read $ARGUMENTS (a feature spec under `.ai/features/`, e.g.
`.ai/features/employees.md`) and produce an implementation plan:

1. Restate the acceptance criteria and map each one to the changes that will
   satisfy it.
2. List ordered tasks. For each task: what changes, which files, and how to
   verify it. Order so `make check` stays green after every task.
3. If the feature spans frontend and backend, the first task pins the API
   contract (routes, request/response shapes) — then backend, then frontend
   against it, so the surfaces cannot drift apart. Event contracts under
   `contracts/events/` are written before any producer or consumer.
4. Call out risks, unknowns, and any decision that deserves an ADR.
5. Specify the test strategy: which tests are added or changed, which gate
   covers them (`make check-api`, `make check-web`, `tests/e2e/`), and the
   failure case being covered.
6. Write the plan into the spec's `## Plan` section and set `Status: approved`.

Do not modify application code.