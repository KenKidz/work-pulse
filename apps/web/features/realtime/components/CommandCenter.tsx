"use client";

/**
 * The command-center wall — live counts, who-is-working rows and the activity
 * rail driven by one Query-cache entry: the REST snapshot (refreshed about
 * every 60s) patched by `presence.upsert` frames over the socket. Display
 * names are joined client-side from the employee directory the web already
 * holds; frames carry identifiers only. Client leaf.
 */

import { CircleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useMemo } from "react";

import { useGetCurrentEmployee } from "@workpulse/api-client";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { COMMAND_CENTER_ROLES } from "@/components/destinations";
import { useAuth } from "@/features/auth/components/AuthGate";
import { useEmployees } from "@/features/employees/hooks/use-employees";

import type { SocketFactory } from "../hooks/use-presence-socket";
import { usePresenceSocket } from "../hooks/use-presence-socket";
import { isForbiddenError, useRealtimeBoard } from "../hooks/use-realtime-board";
import { buildEmployeeDirectory, sortPresenceRows } from "../utils";
import { LiveActivityRail } from "./LiveActivityRail";
import { LiveCounts } from "./LiveCounts";
import { LiveStatusBanner } from "./LiveStatusBanner";
import { PresenceTable } from "./PresenceTable";

/** One page of the directory; employees beyond it fall back to short ids. */
export const DIRECTORY_PAGE_SIZE = 100;

export function CommandCenter({ socketFactory }: { socketFactory?: SocketFactory } = {}) {
  const t = useTranslations("realtime.commandCenter");
  const common = useTranslations("common.actions");
  const router = useRouter();
  const { user } = useAuth();
  const allowed = COMMAND_CENTER_ROLES.includes(user.role);

  useEffect(() => {
    // Direct visits by non-allowed roles never render the wall; the API
    // policy stays the authority and would 403 anyway.
    if (!allowed) {
      router.replace("/dashboard");
    }
  }, [allowed, router]);

  const boardQuery = useRealtimeBoard(allowed);
  const employeesQuery = useEmployees({ page_size: DIRECTORY_PAGE_SIZE });
  // Managers list direct reports only, but the board also covers self — merge
  // the linked employee so the manager's own row can resolve a name.
  const meQuery = useGetCurrentEmployee({
    query: { enabled: allowed && user.employee_id !== null },
  });
  const { status } = usePresenceSocket({ enabled: allowed, socketFactory });

  const employeesResponse = employeesQuery.data;
  const meResponse = meQuery.data;
  const directory = useMemo(
    () =>
      buildEmployeeDirectory(
        employeesResponse && employeesResponse.status === 200 ? employeesResponse.data.items : [],
        meResponse && meResponse.status === 200 ? meResponse.data : null,
      ),
    [employeesResponse, meResponse],
  );

  const response = boardQuery.data;
  const board = response && response.status === 200 ? response.data : null;
  const rows = useMemo(
    () => sortPresenceRows(board?.presence ?? [], directory),
    [board?.presence, directory],
  );

  if (!allowed) {
    // Session user is known, but this role is being redirected — render the
    // same quiet notice as the users page while the replace is in flight.
    return (
      <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>{t("checkingAccess")}</p>
      </div>
    );
  }

  const denied = boardQuery.isError && isForbiddenError(boardQuery.error);

  return (
    <div className="flex flex-col gap-6">
      <LiveStatusBanner status={status} updatedAt={boardQuery.dataUpdatedAt} />

      {boardQuery.isPending ? (
        <div className="flex items-center justify-center gap-2 rounded-md border border-dashed px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>{t("loading")}</p>
        </div>
      ) : boardQuery.isError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertTitle>
            {denied ? t("deniedTitle") : t("errorTitle")}
          </AlertTitle>
          <AlertDescription>
            <p>
              {denied ? t("denied") : t("loadError")}
            </p>
            {!denied ? (
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void boardQuery.refetch()}
              >
                {common("retry")}
              </Button>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : board ? (
        <>
          <LiveCounts counts={board.counts} />
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(17rem,0.8fr)]">
            <PresenceTable rows={rows} directory={directory} />
            <LiveActivityRail activity={board.activity} directory={directory} />
          </div>
        </>
      ) : null}
    </div>
  );
}
