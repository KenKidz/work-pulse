"use client";

/**
 * Connection-state strip for the wall. Live shows the pulse and the last
 * cache update; the degraded state keeps the snapshot visible and says it
 * refreshes about once a minute while the socket reconnects. The live region
 * wraps the state chip only — the "Updated" clock refreshes with every board
 * patch and must not re-announce the whole strip. Client leaf.
 */

import { CircleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Spinner } from "@/components/ui/spinner";

import type { PresenceSocketStatus } from "../hooks/use-presence-socket";
import { formatClockTime } from "../utils";

type LiveStatusBannerProps = {
  status: PresenceSocketStatus;
  /** TanStack's `dataUpdatedAt` (ms) — 0 until the first payload lands. */
  updatedAt: number;
};

export function LiveStatusBanner({ status, updatedAt }: LiveStatusBannerProps) {
  if (status === "live") {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-success/25 bg-success/5 px-4 py-2.5 text-sm">
        <span
          role="status"
          data-testid="live-status"
          className="flex items-center gap-2 font-medium text-success"
        >
          <span aria-hidden="true" className="relative flex size-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-60 motion-reduce:hidden" />
            <span className="relative inline-flex size-2.5 rounded-full bg-success" />
          </span>
          Live
        </span>
        <span className="text-muted-foreground">
          Check-ins and check-outs appear here within seconds.
        </span>
        {updatedAt > 0 ? (
          <span className="ml-auto font-mono text-xs text-muted-foreground tabular-nums">
            Updated {formatClockTime(new Date(updatedAt).toISOString())}
          </span>
        ) : null}
      </div>
    );
  }

  if (status === "connecting") {
    return (
      <Alert role="status">
        <Spinner />
        <AlertTitle>Connecting to live updates…</AlertTitle>
        <AlertDescription>The board shows the latest snapshot meanwhile.</AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert variant="destructive" role="status">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>Live updates unavailable</AlertTitle>
      <AlertDescription>
        <p>
          Showing the latest snapshot — it refreshes about once a minute while we reconnect
          automatically.
        </p>
      </AlertDescription>
    </Alert>
  );
}
