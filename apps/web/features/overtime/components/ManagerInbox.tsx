"use client";

/**
 * The manager-step inbox — overtime claims that reached `requested` in the
 * actor's scope (a manager's team, or the whole tenant for HR/admin). The
 * requester's own claim is listed without actions because the server refuses
 * self-decision; it waits for someone else. The list polls while rows are open
 * so a freshly submitted claim lands without a manual refresh. Client leaf.
 */

import { CircleAlert, Inbox } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuth } from "@/features/auth/components/AuthGate";

import { formatDate, formatHours } from "../dates";
import { useOvertimeDirectory, useOvertimeRequests } from "../hooks/use-overtime";
import { OVERTIME_MANAGER_STEP_ROLES, isForbiddenError, overtimeEmployeeLabel } from "../labels";
import type { OvertimeRequest } from "../types";
import { DecisionDialog, type OvertimeDecisionTarget } from "./DecisionDialog";
import { OvertimePagination } from "./OvertimePagination";

const QUEUE_PAGE_SIZE = 20;

export function ManagerInbox() {
  const { user } = useAuth();
  if (!OVERTIME_MANAGER_STEP_ROLES.includes(user.role)) return null;
  return <ManagerInboxTable currentEmployeeId={user.employee_id ?? null} />;
}

function ManagerInboxTable({ currentEmployeeId }: { currentEmployeeId: string | null }) {
  const [target, setTarget] = useState<OvertimeDecisionTarget | null>(null);
  const [page, setPage] = useState(1);
  const requestsQuery = useOvertimeRequests(
    {
      status: "requested",
      page,
      page_size: QUEUE_PAGE_SIZE,
    },
    { pollWhenEmpty: "fast" },
  );
  const directory = useOvertimeDirectory({
    hasEmployeeLink: currentEmployeeId !== null,
  });

  const requests = requestsQuery.data?.status === 200 ? requestsQuery.data.data.items : [];
  const pageInfo = requestsQuery.data?.status === 200 ? requestsQuery.data.data.page : null;
  const denied = requestsQuery.isError && isForbiddenError(requestsQuery.error);

  return (
    <section aria-labelledby="manager-inbox-title" className="flex flex-col gap-4">
      <div className="space-y-1">
        <h2 id="manager-inbox-title" className="text-lg leading-6 font-semibold tracking-tight">
          Manager approval
        </h2>
        <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
          Claims waiting for the manager step. Your own claims wait for someone else.
        </p>
      </div>

      <Card
        data-testid="manager-inbox"
        className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none"
      >
        {requestsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading overtime requests…</p>
          </div>
        ) : requestsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertTitle>
                {denied ? "Approvals unavailable" : "Could not load overtime requests"}
              </AlertTitle>
              <AlertDescription>
                <p>
                  {denied
                    ? "Managers, HR and admins approve overtime requests."
                    : "Waiting overtime requests could not be loaded."}
                </p>
                {!denied ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void requestsQuery.refetch()}
                  >
                    Retry
                  </Button>
                ) : null}
              </AlertDescription>
            </Alert>
          </div>
        ) : requests.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Inbox}
              message="No overtime requests are waiting for manager approval."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every request field.
            </p>
            <Table
              aria-label="Overtime requests awaiting manager approval"
              containerLabel="Overtime requests awaiting manager approval. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Employee</TableHead>
                  <TableHead>Work date</TableHead>
                  <TableHead>Hours</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Submitted</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.map((request: OvertimeRequest) => {
                  const name = overtimeEmployeeLabel(request.employee_id, directory);
                  const own = request.employee_id === currentEmployeeId;
                  return (
                    <TableRow key={request.id} data-testid="manager-inbox-row">
                      <TableCell className="pl-3 font-medium">{name}</TableCell>
                      <TableCell className="tabular-nums">
                        {formatDate(request.work_date)}
                      </TableCell>
                      <TableCell className="font-mono tabular-nums">
                        {formatHours(request.hours)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {request.reason ? (
                          <span className="block max-w-[16rem] truncate" title={request.reason}>
                            {request.reason}
                          </span>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground tabular-nums">
                        {formatDate(request.created_at)}
                      </TableCell>
                      <TableCell className="pr-3 text-right">
                        {own ? (
                          <span className="text-xs text-muted-foreground">
                            Your own request — someone else must decide.
                          </span>
                        ) : (
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Approve overtime request for ${name}`}
                              onClick={() => setTarget({ request, mode: "approve" })}
                            >
                              Approve
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Reject overtime request for ${name}`}
                              onClick={() => setTarget({ request, mode: "reject" })}
                            >
                              Reject
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>

            {pageInfo === null ? null : (
              <OvertimePagination
                page={pageInfo}
                label="overtime requests awaiting manager approval"
                onPageChange={setPage}
              />
            )}
          </>
        )}
      </Card>

      {target !== null ? (
        <DecisionDialog
          target={target}
          employeeName={overtimeEmployeeLabel(target.request.employee_id, directory)}
          onClose={() => setTarget(null)}
        />
      ) : null}
    </section>
  );
}
