---
description: Create a feature spec from .ai/features/_template.md
---

Create a new feature spec for: $ARGUMENTS

Steps:

1. Read `.ai/product.md` and `.ai/architecture.md` for context, plus any
   relevant ADRs in `.ai/decisions/`. Note anything they do not answer — those
   become questions, not assumptions.
2. Copy `.ai/features/_template.md` to `.ai/features/<slug>.md` — one spec per
   feature, named after the feature (`employees.md`, `attendance.md`, …). Check
   first whether the feature already has a spec.
3. Fill in Problem, Business rules, Scope, Out of scope, Edge cases, and
   testable Acceptance criteria (see `.ai/features/README.md` for the
   lifecycle). Every surface needs at least one failure/edge-case criterion.
4. Set `Surfaces:` — api, web, api+web, api+web+events, api+web+events+workers.
   For full-stack specs, pin the API contract (routes, request/response shapes)
   in the spec before the acceptance criteria.
5. Fill `Surfaces & files` using the locations defined in `.ai/conventions.md`.
6. Ask the user about anything ambiguous. Do not invent product scope.
7. Leave `## Plan` untouched — that belongs to `/plan`.
8. Add a one-line entry to `tasks/TODO.md` under "Now" linking the new spec.

Do not write application code in this step.