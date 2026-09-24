# @workpulse/api-client

Typed HTTP client + TanStack Query hooks for the WorkPulse API.

**Generated** by orval from `contracts/openapi/openapi.json`. Regenerate with
`make gen-api` (repo root) after every API contract change. `src/` is owned by
the generator — never hand-edit it.

| File | Meaning |
| --- | --- |
| `src/index.ts` | endpoint functions + TanStack Query hooks |
| `src/index.schemas.ts` | request/response types |
| `src/mutator/custom-fetch.ts` | the single HTTP transport: base URL + `ApiError` mapping (ADR 0005) |

Consumed by `apps/web` through the `@workpulse/api-client` tsconfig/vite alias
plus a `workspace:*` dependency. pnpm's isolated `node_modules` means this
package must declare everything its generated code imports — today that is
`@tanstack/react-query`; add to `dependencies` here whenever orval starts
emitting imports of a new package.
