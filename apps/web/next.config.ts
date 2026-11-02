import path from "node:path";

import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

const nextConfig: NextConfig = {
  // Deployment (Phase 8): the web image ships `.next/standalone` — a minimal
  // Node server with only the traced runtime files, started by the container.
  output: "standalone",
  // Monorepo without npm workspaces: pin the tracing root to the repository
  // root so Next does not walk up to unrelated lockfiles above this directory.
  // With `standalone`, this keeps the output rooted at the monorepo, so the
  // server lands at `.next/standalone/apps/web/server.js` and workspace
  // packages are traced correctly.
  outputFileTracingRoot: path.join(__dirname, "..", ".."),
};

export default withNextIntl(nextConfig);
