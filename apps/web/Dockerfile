# syntax=docker/dockerfile:1

# Web image: Next.js `output: "standalone"` (deployment business rule 1,
# ADR 0026). Build from the repository root:
#   docker build -f apps/web/Dockerfile -t workpulse-web:local .

# Node 22, not 20: the repository pins pnpm 11.13.1 (`packageManager`), which
# requires Node >= 22.13 (`node:sqlite`) — Node 20 fails the workspace install.
FROM node:22-alpine AS base

ENV NEXT_TELEMETRY_DISABLED=1
# Pinned to the repository's packageManager version.
RUN npm install --global pnpm@11.13.1

WORKDIR /app

FROM base AS deps

# Manifests only: the install layer is reusable while application source
# changes. The filtered install skips the root Playwright devDependency.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/api-client/package.json packages/api-client/package.json
RUN pnpm install --frozen-lockfile --filter workpulse-web...

FROM base AS builder

COPY --from=deps /app ./
COPY apps/web ./apps/web
COPY packages ./packages

# Browser-facing API URL, inlined by Next at build time. The deploy runbook
# documents that changing it requires rebuilding the image.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN pnpm --filter workpulse-web run build \
    && cp -r apps/web/.next/static apps/web/.next/standalone/apps/web/.next/static \
    && if [ -d apps/web/public ]; then cp -r apps/web/public apps/web/.next/standalone/apps/web/public; fi

FROM node:22-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

# The standalone bundle is rooted at the monorepo because
# `outputFileTracingRoot` points there, so the server is `apps/web/server.js`.
# The standalone tree already contains `.next/static` because the builder
# copies it in; a second static COPY would only duplicate the files.
COPY --from=builder --chown=node:node /app/apps/web/.next/standalone ./

USER node

EXPOSE 3000

CMD ["node", "apps/web/server.js"]
