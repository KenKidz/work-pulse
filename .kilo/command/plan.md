---
description: Turn an approved spec into an ordered implementation plan
---

Read $ARGUMENTS (a feature spec under `.ai/features/`, e.g.
`.ai/features/employees.md`) and produce an implementation plan.

You (the main model) do the planning. Use subagents only to look up and check
repo code — never delegate the planning decision itself.

1. Restate the acceptance criteria and map each one to the changes that will
   satisfy it.
2. Delegate repo research to the read-only `researcher` subagent (or the
   built-in `explore` agent): locate the files, symbols, call paths, and
   existing patterns the plan will touch, and confirm they match what the spec
   assumes. Batch independent lookups into one prompt.
3. List ordered tasks. For each task: what changes, which files, and how to
   verify it. Order so `make check` stays green after every task.
4. If the feature spans frontend and backend, the first task pins the API
   contract (routes, request/response shapes) — then backend, then frontend
   against it, so the surfaces cannot drift apart. Event contracts under
   `contracts/events/` are written before any producer or consumer.
5. Call out risks, unknowns, and any decision that deserves an ADR.
6. Specify the test strategy: which tests are added or changed, which gate
   covers them (`make check-api`, `make check-web`, `tests/e2e/`), and the
   failure case being covered.
7. Write the plan into the spec's `## Plan` section and set `Status: approved`.

Do not modify application code.
