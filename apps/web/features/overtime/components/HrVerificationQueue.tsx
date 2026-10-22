"use client";

/**
 * The HR-step queue — manager-approved claims awaiting verification, plus
 * verified rows still inside payroll's un-verify window (`hr_verified` can be
 * rejected by HR before a finalize counts it, PO Decision 6). Verify accepts
 * the requested hours exactly; reject is the only way to change a claim. Each
 * section paginates independently, so a long backlog never hides actionable
 * rows. The requester's own row is listed without actions (self-decision is
 * `403`). Decision queries poll while mounted (fast when pending, slow for the
 * payroll-bound window). Client leaf.
 */

import { CircleAlert, Inbox } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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

import { formatDate, formatHours } from "../dates";
import { useOvertimeDirectory, useOvertimeRequests } from "../hooks/use-overtime";
import {
  OVERTIME_HR_STEP_ROLES,
  isForbiddenError,
  overtimeEmployeeLabel,
  overtimeStatusLabel,
  overtimeStatusVariant,
} from "../labels";
import type { OvertimeRequest, Page } from "../types";
import { DecisionDialog, type OvertimeDecisionTarget } from "./DecisionDialog";
import { OvertimePagination } from "./OvertimePagination";

const QUEUE_PAGE_SIZE = 20;

type QueueSection = "pending" | "verified";

export function HrVerificationQueue() {
  const { user } = useAuth();
  if (!OVERTIME_HR_STEP_ROLES.includes(user.role)) return null;
  return <HrVerificationQueueTable currentEmployeeId={user.employee_id ?? null} />;
}

function HrVerificationQueueTable({ currentEmployeeId }: { currentEmployeeId: string | null }) {
  const [target, setTarget] = useState<OvertimeDecisionTarget | null>(null);
  const [pendingPage, setPendingPage] = useState(1);
  const [verifiedPage, setVerifiedPage] = useState(1);
  const pendingQuery = useOvertimeRequests(
    {
      status: "manager_approved",
      page: pendingPage,
      page_size: QUEUE_PAGE_SIZE,
    },
    { pollWhenEmpty: "fast" },
  );
  const verifiedQuery = useOvertimeRequests(
    {
      status: "hr_verified",
      page: verifiedPage,
      page_size: QUEUE_PAGE_SIZE,
    },
    { pollWhenEmpty: "slow" },
  );
  const directory = useOvertimeDirectory({
    hasEmployeeLink: currentEmployeeId !== null,
  });

  const pending = pendingQuery.data?.status === 200 ? pendingQuery.data.data.items : [];
  const pendingPageInfo = pendingQuery.data?.status === 200 ? pendingQuery.data.data.page : null;
  const verified = verifiedQuery.data?.status === 200 ? verifiedQuery.data.data.items : [];
  const verifiedPageInfo = verifiedQuery.data?.status === 200 ? verifiedQuery.data.data.page : null;

  const loading = pendingQuery.isPending || verifiedQuery.isPending;
  const failed = pendingQuery.isError || verifiedQuery.isError;
  const denied = isForbiddenError(pendingQuery.error) || isForbiddenError(verifiedQuery.error);

  function retry() {
    void pendingQuery.refetch();
    void verifiedQuery.refetch();
  }

  return (
    <section aria-labelledby="hr-verification-title" className="flex flex-col gap-4">
      <div className="space-y-1">
        <h2 id="hr-verification-title" className="text-lg leading-6 font-semibold tracking-tight">
          HR verification
        </h2>
        <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
          Manager-approved claims waiting for HR verification, and verified rows still inside
          payroll’s un-verify window.
        </p>
      </div>

      <Card
        data-testid="hr-verification"
        className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none"
      >
        {loading ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading overtime requests…</p>
          </div>
        ) : failed ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertTitle>
                {denied ? "Verification unavailable" : "Could not load overtime requests"}
              </AlertTitle>
              <AlertDescription>
                <p>
                  {denied
                    ? "HR, admins and super admins verify overtime requests."
                    : "The verification queue could not be loaded."}
                </p>
                {!denied ? (
                  <Button variant="outline" size="sm" className="mt-2" onClick={retry}>
                    Retry
                  </Button>
                ) : null}
              </AlertDescription>
            </Alert>
          </div>
        ) : pending.length === 0 && verified.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Inbox}
              message="No overtime requests are waiting for HR verification."
            />
          </div>
        ) : (
          <>
            <QueueTable
              section="pending"
              title="Awaiting verification"
              description="Manager-approved claims. Verify accepts the requested hours exactly."
              emptyMessage="Nothing is waiting for HR verification."
              rows={pending}
              pageInfo={pendingPageInfo}
              paginationLabel="overtime requests awaiting HR verification"
              onPageChange={setPendingPage}
              currentEmployeeId={currentEmployeeId}
              directory={directory}
              onTarget={setTarget}
            />
            <QueueTable
              section="verified"
              title="Verified — payroll un-verify window"
              description="Payroll counts these rows. Reject before a finalize to un-verify one."
              emptyMessage="No verified rows inside the payroll un-verify window."
              rows={verified}
              pageInfo={verifiedPageInfo}
              paginationLabel="verified overtime requests"
              onPageChange={setVerifiedPage}
              currentEmployeeId={currentEmployeeId}
              directory={directory}
              onTarget={setTarget}
            />
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

function QueueTable({
  section,
  title,
  description,
  emptyMessage,
  rows,
  pageInfo,
  paginationLabel,
  onPageChange,
  currentEmployeeId,
  directory,
  onTarget,
}: {
  section: QueueSection;
  title: string;
  description: string;
  emptyMessage: string;
  rows: OvertimeRequest[];
  pageInfo: Page | null;
  paginationLabel: string;
  onPageChange: (page: number) => void;
  currentEmployeeId: string | null;
  directory: ReadonlyMap<string, string>;
  onTarget: (target: OvertimeDecisionTarget) => void;
}) {
  const pending = section === "pending";
  const tableLabel = pending
    ? "Overtime requests awaiting HR verification"
    : "Verified overtime requests inside the un-verify window";

  return (
    <div className="border-b last:border-b-0">
      <div className="space-y-1 px-4 py-3 sm:px-5">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="max-w-2xl text-xs leading-5 text-pretty text-muted-foreground">
          {description}
        </p>
      </div>

      {rows.length === 0 ? (
        <p className="px-4 pb-4 text-sm text-muted-foreground sm:px-5">{emptyMessage}</p>
      ) : (
        <>
          <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
            Swipe sideways to see every request field.
          </p>
          <Table
            aria-label={tableLabel}
            containerLabel={`${tableLabel}. Scroll horizontally to read all columns.`}
          >
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-3">Employee</TableHead>
                <TableHead>Work date</TableHead>
                <TableHead>Hours</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Reason</TableHead>
                <TableHead>Manager note</TableHead>
                <TableHead className="pr-3 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((request) => {
                const name = overtimeEmployeeLabel(request.employee_id, directory);
                const own = request.employee_id === currentEmployeeId;
                return (
                  <TableRow
                    key={request.id}
                    data-testid="hr-verification-row"
                    data-status={request.status}
                  >
                    <TableCell className="pl-3 font-medium">{name}</TableCell>
                    <TableCell className="tabular-nums">{formatDate(request.work_date)}</TableCell>
                    <TableCell className="font-mono tabular-nums">
                      {formatHours(request.hours)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={overtimeStatusVariant[request.status]}>
                        {overtimeStatusLabel[request.status]}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {request.reason ? (
                        <span className="block max-w-[14rem] truncate" title={request.reason}>
                          {request.reason}
                        </span>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {request.manager_note ? (
                        <span className="block max-w-[14rem] truncate" title={request.manager_note}>
                          {request.manager_note}
                        </span>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="pr-3 text-right">
                      {own ? (
                        <span className="text-xs text-muted-foreground">
                          Your own request — someone else must decide.
                        </span>
                      ) : (
                        <div className="flex justify-end gap-2">
                          {pending ? (
                            <Button
                              variant="outline"
                              size="sm"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Verify overtime request for ${name}`}
                              onClick={() => onTarget({ request, mode: "verify" })}
                            >
                              Verify
                            </Button>
                          ) : null}
                          <Button
                            variant="outline"
                            size="sm"
                            className="min-h-11 sm:min-h-7"
                            aria-label={`Reject overtime request for ${name}`}
                            onClick={() => onTarget({ request, mode: "reject" })}
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
              label={paginationLabel}
              onPageChange={onPageChange}
            />
          )}
        </>
      )}
    </div>
  );
}
