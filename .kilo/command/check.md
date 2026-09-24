---
description: Run the verification gate and report what passed
---

Verify the current change:

1. Run `make check` (API pytest gate + web typecheck gate) and report the exact
   result. If a gate fails, show the failing output and fix the cause — not the
   test — unless the test itself is wrong, in which case say why in one
   sentence. To isolate a single surface: `make check-api` / `make check-web`.
2. Run `git status --short` and `git diff --stat` to confirm only intended files
   changed. Flag anything unrelated.
3. Against the relevant spec's acceptance criteria, mark which ones now hold and
   tick them in the spec.
4. Report per surface (api, web, events, e2e): what passed, what changed, what is
   still unverified, and any failures honestly — no optimistic summaries.
   Criteria covered only by manual verification must be reported as such, not
   as verified.

If `$ARGUMENTS` names a spec, verify against it; otherwise verify the working tree.