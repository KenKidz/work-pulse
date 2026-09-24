# Feature: <name>

- **Status:** draft
- **Phase:** 1
- **Size:** S | M | L
- **Surfaces:** api | web | api+web | api+web+events | api+web+events+workers
- **Links:** <!-- ADRs, related features, .ai/domain.md context, brief sections -->

## Problem

<!-- Why this feature exists. One short paragraph. -->

## Business rules

<!-- The rules the code must obey. If a rule is not written here, it does not
     exist and must not be implemented. Mark proposals as (proposed). -->

-

## Scope

-

## Out of scope

<!-- Prevents scope creep. Be explicit about what this feature does NOT do. -->

-

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/<name>/` |
| API router | `apps/api/app/api/v1/<name>.py` |
| Events | `contracts/events/<name>/` (if any) |
| Workers | `workers/<name>/` (if any — Phase 3+) |
| Web feature | `apps/web/features/<name>/` |
| Web routes | `apps/web/app/(dashboard)/<name>/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/` |

## Roles & access

<!-- Which roles may call each endpoint — see .ai/security.md matrix. -->

## API contract

<!-- Pin routes and request/response shapes BEFORE coding. For full-stack
     features this is the first thing that gets frozen. -->

## Events

<!-- Versioned contracts and payload sketches. Contract before producer. -->

## Edge cases

-

## Acceptance criteria

<!-- Testable statements. Each maps to at least one test or a concrete command.
     At least one failure case per surface is mandatory, plus tenant isolation
     and a denied-role case for anything mutating. -->

- [ ] API: given …, when …, then …
- [ ] API (failure): given …, when …, then …
- [ ] API (tenant isolation): given …, when …, then 404
- [ ] Web: given …, when …, then …

## Decisions needed

<!-- Open questions for the product owner. Do not guess: list them, propose a
     default, and wait for an answer. When every item is resolved, rename this
     section to `## Decisions (PO, <date>)` recording outcome + rationale — only
     then may /plan set `Status: approved`. -->

-

## Plan

<!-- Filled in by /plan. Ordered tasks naming files + verification. -->

_Not planned yet._