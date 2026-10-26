import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: [
      // The test-only translation runtime reads the real English catalog.
      {
        find: /^next-intl$/,
        replacement: path.resolve(import.meta.dirname, "tests/mocks/next-intl.tsx"),
      },
      {
        find: /^next-intl\/server$/,
        replacement: path.resolve(import.meta.dirname, "tests/mocks/next-intl-server.ts"),
      },
      {
        find: /^@\//,
        replacement: `${path.resolve(import.meta.dirname)}/`,
      },
      {
        find: "@workpulse/api-client",
        replacement: path.resolve(import.meta.dirname, "../../packages/api-client/src"),
      },
    ],
  },
});
