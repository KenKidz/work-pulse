"use client";

/**
 * The actor's own leave requests — submit, follow each request to its
 * decision, and withdraw a `PENDING` one. Visibility is own-only by the API;
 * no employee link means no request capability (HR/admin accounts without a
 * linked employee still see the team inbox). Client leaf.
 */

import { CalendarPlus, CircleAlert, Inbox } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
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

import { formatDate } from "../dates";
import { leaveWithdrawError, useLeaveRequests, useWithdrawLeaveAction } from "../hooks/use-leave";
import { leaveStatusLabel, leaveStatusVariant, leaveTypeLabel } from "../labels";
import type { LeaveRequest } from "../types";
import { LeaveRequestDialog } from "./LeaveRequestDialog";

export const MY_REQUESTS_PAGE_SIZE = 20;

export function MyRequestsTable() {
  const { user } = useAuth();
  const employeeId = user.employee_id ?? null;
  const [page, setPage] = useState(1);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const withdraw = useWithdrawLeaveAction();

  const requestsQuery = useLeaveRequests(
    { employee_id: employeeId ?? undefined, page, page_size: MY_REQUESTS_PAGE_SIZE },
    employeeId !== null,
  );

  const response = requestsQuery.data;
  const requests = response && response.status === 200 ? response.data.items : [];
  const pageInfo = response && response.status === 200 ? response.data.page : null;

  async function onWithdraw(request: LeaveRequest) {
    setActionError(null);
    try {
      await withdraw.mutateAsync({ id: request.id });
    } catch (error) {
      setActionError(leaveWithdrawError(error));
    }
  }

  if (employeeId === null) {
    return (
      <section aria-labelledby="my-requests-title" className="flex flex-col gap-4">
        <h2 id="my-requests-title" className="text-lg leading-6 font-semibold tracking-tight">
          My requests
        </h2>
        <Card className="gap-0 rounded-md border-border py-0 shadow-none">
          <div className="p-4">
            <EmptyState
              icon={Inbox}
              message="Your account isn’t linked to an employee record, so you can’t request leave."
            />
          </div>
        </Card>
      </section>
    );
  }

  return (
    <section aria-labelledby="my-requests-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h2 id="my-requests-title" className="text-lg leading-6 font-semibold tracking-tight">
            My requests
          </h2>
          <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
            Submit time off and follow each request to its decision.
          </p>
        </div>
        <Button
          className="min-h-11"
          onClick={() => {
            // A fresh request lands on the first page of the list.
            setPage(1);
            setDialogOpen(true);
          }}
        >
          <CalendarPlus />
          Request leave
        </Button>
      </div>

      {actionError ? (
        <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {actionError}
        </p>
      ) : null}

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {requestsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading your leave requests…</p>
          </div>
        ) : requestsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load your leave requests.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void requestsQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : requests.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={CalendarPlus}
              message="No leave requests yet. Request leave and it will appear here."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every request field.
            </p>
            <Table
              aria-label="My leave requests"
              containerLabel="My leave requests. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Type</TableHead>
                  <TableHead>Dates</TableHead>
                  <TableHead>Days</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.map((request) => (
                  <TableRow key={request.id}>
                    <TableCell className="pl-3">
                      {leaveTypeLabel[request.leave_type_code]}
                    </TableCell>
                    <TableCell className="text-sm tabular-nums">
                      {formatDate(request.start_date)} – {formatDate(request.end_date)}
                    </TableCell>
                    <TableCell className="tabular-nums">{request.days}</TableCell>
                    <TableCell>
                      <Badge
                        variant={leaveStatusVariant[request.status]}
                        data-status={request.status}
                      >
                        {leaveStatusLabel[request.status]}
                      </Badge>
                      {request.decision_note ? (
                        <span
                          className="mt-1 block max-w-[16rem] truncate text-xs text-muted-foreground"
                          title={request.decision_note}
                        >
                          {request.decision_note}
                        </span>
                      ) : null}
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
                      {request.status === "pending" ? (
                        <Button
                          variant="outline"
                          size="sm"
                          className="min-h-11 sm:min-h-7"
                          aria-label={`Withdraw leave request starting ${request.start_date}`}
                          disabled={withdraw.isPending}
                          onClick={() => void onWithdraw(request)}
                        >
                          Withdraw
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {pageInfo && pageInfo.total_pages > 1 ? (
              <div className="flex items-center justify-end gap-2 border-t px-4 py-3 text-sm">
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Previous page"
                  disabled={pageInfo.page <= 1}
                  onClick={() => setPage(pageInfo.page - 1)}
                >
                  Previous
                </Button>
                <span className="text-muted-foreground">
                  Page {pageInfo.page} of {pageInfo.total_pages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Next page"
                  disabled={pageInfo.page >= pageInfo.total_pages}
                  onClick={() => setPage(pageInfo.page + 1)}
                >
                  Next
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Card>

      {dialogOpen ? <LeaveRequestDialog open onOpenChange={setDialogOpen} /> : null}
    </section>
  );
}
