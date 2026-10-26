"use client";

/**
 * The actor's own overtime claims — submit, filter by status/work date, follow
 * each claim to its decision and withdraw a pre-verification one. Visibility
 * is own-only by the API; no employee link means no claim capability. The list
 * polls while a claim is open so a manager/HR decision made elsewhere lands
 * without a manual refresh. Client leaf.
 */

import { CalendarPlus, CircleAlert, Inbox } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import {
  overtimeWithdrawError,
  useOvertimeRequests,
  useWithdrawOvertimeAction,
} from "../hooks/use-overtime";
import { canWithdraw, overtimeStatusVariant } from "../labels";
import type { OvertimeRequest, OvertimeRequestStatus } from "../types";
import { OvertimePagination } from "./OvertimePagination";
import { OvertimeRequestDialog } from "./OvertimeRequestDialog";

export const MY_OVERTIME_PAGE_SIZE = 20;

const ANY_STATUS = "any";

const STATUS_OPTIONS: OvertimeRequestStatus[] = [
  "requested",
  "manager_approved",
  "hr_verified",
  "included_in_payroll",
  "rejected",
  "withdrawn",
];

type Filters = {
  status?: OvertimeRequestStatus;
  from?: string;
  to?: string;
};

/** The latest decision note for the requester (HR's note outranks the manager's). */
function decisionNote(request: OvertimeRequest): string | null {
  return request.hr_note ?? request.manager_note ?? null;
}

function FiltersRow({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (next: Filters) => void;
}) {
  const t = useTranslations("overtime.myRequests");
  const statusT = useTranslations("overtime.status");

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="overtime-filters">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 sm:flex">
        <Input
          aria-label={t("fromWorkDate")}
          type="date"
          className="w-full sm:w-40"
          value={filters.from ?? ""}
          onChange={(event) => onChange({ ...filters, from: event.target.value || undefined })}
        />
        <span className="text-sm text-muted-foreground">{t("to")}</span>
        <Input
          aria-label={t("toWorkDate")}
          type="date"
          className="w-full sm:w-40"
          value={filters.to ?? ""}
          onChange={(event) => onChange({ ...filters, to: event.target.value || undefined })}
        />
      </div>

      <Select
        value={filters.status ?? ANY_STATUS}
        onValueChange={(value) =>
          onChange({
            ...filters,
            status: value === ANY_STATUS ? undefined : (value as OvertimeRequestStatus),
          })
        }
      >
        <SelectTrigger aria-label={t("statusAria")} className="w-full sm:w-48">
          <SelectValue placeholder={t("anyStatus")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY_STATUS}>{t("anyStatus")}</SelectItem>
          {STATUS_OPTIONS.map((status) => (
            <SelectItem key={status} value={status}>
              {statusT(status)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function MyOvertimeTable() {
  const t = useTranslations("overtime");
  const locale = useLocale();
  const common = useTranslations("common.actions");
  const errors = useTranslations("overtime.errors");
  const { user } = useAuth();
  const employeeId = user.employee_id ?? null;
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Filters>({});
  const [dialogOpen, setDialogOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const withdraw = useWithdrawOvertimeAction();

  const requestsQuery = useOvertimeRequests(
    {
      employee_id: employeeId ?? undefined,
      status: filters.status,
      from: filters.from,
      to: filters.to,
      page,
      page_size: MY_OVERTIME_PAGE_SIZE,
    },
    { enabled: employeeId !== null },
  );

  const response = requestsQuery.data;
  const requests = response && response.status === 200 ? response.data.items : [];
  const pageInfo = response && response.status === 200 ? response.data.page : null;

  function changeFilters(next: Filters) {
    setFilters(next);
    setPage(1);
  }

  async function onWithdraw(request: OvertimeRequest) {
    setActionError(null);
    try {
      await withdraw.mutateAsync({ id: request.id });
    } catch (error) {
      setActionError(overtimeWithdrawError(error, errors));
    }
  }

  if (employeeId === null) {
    return (
      <section aria-labelledby="my-overtime-title" className="flex flex-col gap-4">
        <h2 id="my-overtime-title" className="text-lg leading-6 font-semibold tracking-tight">
          {t("myRequests.title")}
        </h2>
        <Card className="gap-0 rounded-md border-border py-0 shadow-none">
          <div className="p-4">
            <EmptyState
              icon={Inbox}
              message={t("myRequests.noEmployee")}
            />
          </div>
        </Card>
      </section>
    );
  }

  return (
    <section aria-labelledby="my-overtime-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h2 id="my-overtime-title" className="text-lg leading-6 font-semibold tracking-tight">
            {t("myRequests.title")}
          </h2>
          <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
            {t("myRequests.description")}
          </p>
        </div>
        <Button
          className="min-h-11"
          onClick={() => {
            // A fresh claim lands on the first page of the list.
            setPage(1);
            setDialogOpen(true);
          }}
        >
          <CalendarPlus />
          {t("myRequests.request")}
        </Button>
      </div>

      {actionError ? (
        <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {actionError}
        </p>
      ) : null}

      <Card
        data-testid="my-overtime"
        className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none"
      >
        <div className="border-b bg-muted/50 px-4 py-3 sm:px-5">
          <FiltersRow filters={filters} onChange={changeFilters} />
        </div>

        {requestsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("myRequests.loading")}</p>
          </div>
        ) : requestsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("myRequests.loadError")}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void requestsQuery.refetch()}
                >
                  {common("retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : requests.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={CalendarPlus}
              message={
                filters.status === undefined &&
                filters.from === undefined &&
                filters.to === undefined
                  ? t("myRequests.empty")
                  : t("myRequests.emptyFiltered")
              }
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              {t("myRequests.swipe")}
            </p>
            <Table
              aria-label={t("myRequests.label")}
              containerLabel={t("myRequests.containerLabel")}
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("myRequests.columns.workDate")}</TableHead>
                  <TableHead>{t("myRequests.columns.hours")}</TableHead>
                  <TableHead>{t("myRequests.columns.status")}</TableHead>
                  <TableHead>{t("myRequests.columns.reason")}</TableHead>
                  <TableHead>{t("myRequests.columns.submitted")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("myRequests.columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.map((request) => {
                  const note = decisionNote(request);
                  return (
                    <TableRow
                      key={request.id}
                      data-testid="my-overtime-row"
                      data-status={request.status}
                    >
                      <TableCell className="pl-3 tabular-nums">
                        {formatDate(request.work_date, locale)}
                      </TableCell>
                      <TableCell className="font-mono tabular-nums">
                        {formatHours(request.hours, locale)}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={overtimeStatusVariant[request.status]}
                          data-status={request.status}
                        >
                          {t(`status.${request.status}` as never)}
                        </Badge>
                        {note ? (
                          <span
                            className="mt-1 block max-w-[16rem] truncate text-xs text-muted-foreground"
                            title={note}
                          >
                            {note}
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
                        {formatDate(request.created_at, locale)}
                      </TableCell>
                      <TableCell className="pr-3 text-right">
                        {canWithdraw(request.status) ? (
                          <Button
                            variant="outline"
                            size="sm"
                            className="min-h-11 sm:min-h-7"
                            aria-label={t("myRequests.withdrawAria", { date: request.work_date })}
                            disabled={withdraw.isPending}
                            onClick={() => void onWithdraw(request)}
                          >
                            {t("myRequests.withdraw")}
                          </Button>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
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
                label={t("myRequests.paginationLabel")}
                onPageChange={setPage}
              />
            )}
          </>
        )}
      </Card>

      {dialogOpen ? <OvertimeRequestDialog open onOpenChange={setDialogOpen} /> : null}
    </section>
  );
}
