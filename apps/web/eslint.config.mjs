import { defineConfig, globalIgnores } from "eslint/config";
import prettier from "eslint-config-prettier";
import coreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

export default defineConfig([
  ...coreWebVitals,
  ...nextTypescript,
  // Formatting is Prettier's job — turn off ESLint's conflicting stylistic rules.
  prettier,
  globalIgnores([".next/", "coverage/", "next-env.d.ts"]),
  {
    rules: {
      "no-console": ["warn", { allow: ["warn", "error"] }],
    },
  },
]);
