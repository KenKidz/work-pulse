import { defineConfig } from "orval";

/**
 * Generates the typed API client + TanStack Query hooks into
 * packages/api-client from contracts/openapi/openapi.json.
 *
 * Run via `make gen-api` (dumps the OpenAPI spec first) or
 * `npm run gen:api` (regenerates from the checked-in spec only).
 * Generated files are never hand-edited.
 */
export default defineConfig({
  workpulse: {
    input: "../../contracts/openapi/openapi.json",
    output: {
      target: "../../packages/api-client/src/index.ts",
      client: "react-query",
      httpClient: "fetch",
      mode: "split",
      // Pin orval's formatter so regeneration is byte-stable against the
      // committed client (the standing `make gen-api` churn TODO).
      formatter: "prettier",
      override: {
        // All HTTP goes through our single transport: base URL + ApiError mapping.
        mutator: {
          path: "../../packages/api-client/src/mutator/custom-fetch.ts",
          name: "customFetch",
        },
      },
    },
  },
});
