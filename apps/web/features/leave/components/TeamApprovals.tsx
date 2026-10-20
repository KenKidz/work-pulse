"use client";

/**
 * The approval inbox — pending requests the actor may decide (manager:
 * self + direct reports; HR/admin/super admin: tenant-wide). Own requests are
 * listed but never actionable: the API refuses self-approval with a 403, and
 * the dialog renders that refusal as a visible error state. Names are joined
 * client-side from the employee directory; the wire carries identifiers only.
 * Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert, Inbox } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/empty-state";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/features/auth/components/AuthGate";

import { formatDate } from "../dates";
import {
  isForbiddenError,
  leaveDecisionError,
  useApproveLeaveAction,
  useLeaveDirectory,
  useLeaveRequests,
  useRejectLeaveAction,
} from "../hooks/use-leave";
import { LEAVE_APPROVER_ROLES, leaveTypeLabel } from "../labels";
import { leaveDecisionFormSchema, type LeaveDecisionFormValues } from "../schema";
import type { LeaveRequest } from "../types";

const PENDING_PAGE_SIZE = 100;

type DecisionMode = "approve" | "reject";

type DecisionTarget = { request: LeaveRequest; mode: DecisionMode };

export function TeamApprovals() {
  const { user } = useAuth();
  if (!LEAVE_APPROVER_ROLES.includes(user.role)) return null;
  return <TeamApprovalsInbox currentEmployeeId={user.employee_id ?? null} />;
}

function TeamApprovalsInbox({ currentEmployeeId }: { currentEmployeeId: string | null }) {
  const [target, setTarget] = useState<DecisionTarget | null>(null);
  const requestsQuery = useLeaveRequests({
    status: "pending",
    page: 1,
    page_size: PENDING_PAGE_SIZE,
  });
  const directoryQuery = useLeaveDirectory({
    hasEmployeeLink: currentEmployeeId !== null,
  });

  const requests = requestsQuery.data?.status === 200 ? requestsQuery.data.data.items : [];
  const denied = requestsQuery.isError && isForbiddenError(requestsQuery.error);
  const employees = useMemo(
    () =>
      directoryQuery.employees.data?.status === 200 ? directoryQuery.employees.data.data.items : [],
    [directoryQuery.employees.data],
  );
  const me = directoryQuery.me.data?.status === 200 ? directoryQuery.me.data.data : null;

  const directory = useMemo(() => {
    const names = new Map<string, string>();
    for (const employee of employees) {
      names.set(employee.id, `${employee.first_name} ${employee.last_name}`.trim());
    }
    if (me !== null) {
      names.set(me.id, `${me.first_name} ${me.last_name}`.trim());
    }
    return names;
  }, [employees, me]);

  function employeeName(employeeId: string): string {
    const name = directory.get(employeeId);
    return name !== undefined && name.length > 0 ? name : `Employee …${employeeId.slice(-4)}`;
  }

  return (
    <section aria-labelledby="team-approvals-title" className="flex flex-col gap-4">
      <div className="space-y-1">
        <h2 id="team-approvals-title" className="text-lg leading-6 font-semibold tracking-tight">
          Team approvals
        </h2>
        <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
          Pending requests you can decide. Your own requests wait for someone else.
        </p>
      </div>

      <Card
        data-testid="team-approvals"
        className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none"
      >
        {requestsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading pending requests…</p>
          </div>
        ) : requestsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertTitle>
                {denied ? "Approvals unavailable" : "Could not load pending leave requests"}
              </AlertTitle>
              <AlertDescription>
                <p>
                  {denied
                    ? "Managers, HR and admins decide leave requests."
                    : "Pending requests could not be loaded."}
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
            <EmptyState icon={Inbox} message="No pending leave requests in your scope." />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every request field.
            </p>
            <Table
              aria-label="Pending leave requests"
              containerLabel="Pending leave requests. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Employee</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Dates</TableHead>
                  <TableHead>Days</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.map((request) => {
                  const name = employeeName(request.employee_id);
                  const own = request.employee_id === currentEmployeeId;
                  return (
                    <TableRow key={request.id}>
                      <TableCell className="pl-3 font-medium">{name}</TableCell>
                      <TableCell>{leaveTypeLabel[request.leave_type_code]}</TableCell>
                      <TableCell className="text-sm tabular-nums">
                        {formatDate(request.start_date)} – {formatDate(request.end_date)}
                      </TableCell>
                      <TableCell className="tabular-nums">{request.days}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {request.reason ? (
                          <span className="block max-w-[16rem] truncate" title={request.reason}>
                            {request.reason}
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
                            <Button
                              variant="outline"
                              size="sm"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Approve leave request for ${name}`}
                              onClick={() => setTarget({ request, mode: "approve" })}
                            >
                              Approve
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Reject leave request for ${name}`}
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
          </>
        )}
      </Card>

      {target !== null ? (
        <DecisionDialog
          target={target}
          employeeName={employeeName(target.request.employee_id)}
          onClose={() => setTarget(null)}
        />
      ) : null}
    </section>
  );
}

function DecisionDialog({
  target,
  employeeName,
  onClose,
}: {
  target: DecisionTarget;
  employeeName: string;
  onClose: () => void;
}) {
  const approve = useApproveLeaveAction();
  const reject = useRejectLeaveAction();
  const mutation = target.mode === "approve" ? approve : reject;
  const [rootError, setRootError] = useState<string | null>(null);

  const form = useForm<LeaveDecisionFormValues>({
    resolver: zodResolver(leaveDecisionFormSchema),
    defaultValues: { decision_note: "" },
  });

  async function onSubmit(values: LeaveDecisionFormValues) {
    setRootError(null);
    try {
      await mutation.mutateAsync({
        id: target.request.id,
        data: {
          decision_note: values.decision_note?.trim() ? values.decision_note.trim() : null,
        },
      });
      onClose();
    } catch (error) {
      setRootError(leaveDecisionError(error));
    }
  }

  const approving = target.mode === "approve";

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{approving ? "Approve leave request" : "Reject leave request"}</DialogTitle>
          <DialogDescription>
            {employeeName} · {formatDate(target.request.start_date)} –{" "}
            {formatDate(target.request.end_date)} · {target.request.days}{" "}
            {target.request.days === 1 ? "day" : "days"}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="decision_note"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Decision note (optional)</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value ?? ""}
                      rows={3}
                      placeholder="Explain the decision for the requester."
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {rootError ? (
              <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {rootError}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant={approving ? "default" : "destructive"}
                disabled={mutation.isPending}
              >
                {mutation.isPending
                  ? approving
                    ? "Approving…"
                    : "Rejecting…"
                  : approving
                    ? "Approve request"
                    : "Reject request"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
