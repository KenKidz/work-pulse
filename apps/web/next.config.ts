import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Monorepo without npm workspaces: pin the tracing root to the repository
  // root so Next does not walk up to unrelated lockfiles above this directory.
  outputFileTracingRoot: path.join(__dirname, "..", ".."),
};

export default nextConfig;