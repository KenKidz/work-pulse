# @workpulse/api-client

Shared, typed HTTP client for the WorkPulse API.

**Status: placeholder (Phase 1).** Until the web app moves to a workspace setup,
`apps/web/lib/api/client.ts` is the interim client. This package becomes the
single client when it is generated from `contracts/openapi/openapi.json` — see
`.ai/architecture.md`.

Nothing may depend on this package until it has real code and tests.