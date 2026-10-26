"use client";

/**
 * Jobs tab — the hiring openings with status/department/search filters,
 * pagination, the create/edit dialog and the open/close transitions. Loading,
 * empty and error states are part of the surface; a failed transition keeps
 * the row unchanged and shows the mapped server sentence. Client leaf.
 */

import { Briefcase, CircleAlert, Plus, Search } from "lucide-react";
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

import { useDebouncedValue } from "@/hooks/use-debounced-value";

import { formatRecruitmentTimestamp } from "../dates";
import { jobStatusVariant, organizationLabel } from "../labels";
import { useJobs, useRecruitmentDirectory } from "../hooks/use-recruitment";
import type { Department, Job, JobStatus, ListJobsParams } from "../types";
import { JobFormDialog } from "./JobFormDialog";
import { JobStateDialog, type JobStateAction } from "./JobStateDialog";
import { RecruitmentPagination } from "./RecruitmentPagination";

export const ANY = "any";

/** Page size for the jobs list query. */
export const JOBS_PAGE_SIZE = 20;

/**
 * Filter bar. Clearing a filter must drop the parameter (`undefined`), never
 * send `null`: the generated client only omits undefined and serializes null
 * as the literal string "null" → the API 422s on enum/UUID parsing.
 */
function JobsFilters({
  params,
  departments,
  onChange,
}: {
  params: ListJobsParams;
  departments: Department[];
  onChange: (next: Partial<ListJobsParams>) => void;
}) {
  const t = useTranslations("recruitment.jobs.filters");
  const status = useTranslations("recruitment.status.job");

  const activeDepartments = departments.filter(
    (department) => department.archived_at === null || department.archived_at === undefined,
  );

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="job-filters">
      <div className="relative w-full sm:w-64">
        <Search className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label={t("searchAria")}
          placeholder={t("searchPlaceholder")}
          className="w-full pl-8"
          value={params.q ?? ""}
          onChange={(event) => onChange({ q: event.target.value || undefined })}
        />
      </div>

      <Select
        value={params.status ?? ANY}
        onValueChange={(value) =>
          onChange({ status: value === ANY ? undefined : (value as JobStatus), page: 1 })
        }
      >
        <SelectTrigger aria-label={t("statusAria")} className="w-full sm:w-40">
          <SelectValue placeholder={t("anyStatus")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("anyStatus")}</SelectItem>
          <SelectItem value="draft">{status("draft")}</SelectItem>
          <SelectItem value="open">{status("open")}</SelectItem>
          <SelectItem value="closed">{status("closed")}</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={params.department_id ?? ANY}
        onValueChange={(value) =>
          onChange({ department_id: value === ANY ? undefined : value, page: 1 })
        }
      >
        <SelectTrigger aria-label={t("departmentAria")} className="w-full sm:w-48">
          <SelectValue placeholder={t("allDepartments")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("allDepartments")}</SelectItem>
          {activeDepartments.map((department) => (
            <SelectItem key={department.id} value={department.id}>
              {department.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

type OpenState = { job: Job; action: JobStateAction };

export function JobsTable() {
  const t = useTranslations("recruitment.jobs");
  const common = useTranslations("recruitment.actions");
  const status = useTranslations("recruitment.status.job");
  const fallbacks = useTranslations("recruitment.fallbacks");
  const locale = useLocale();
  const [params, setParams] = useState<ListJobsParams>({
    page: 1,
    page_size: JOBS_PAGE_SIZE,
  });
  // The search box state — fed to the query through useDebouncedValue so a
  // typed query costs one list request on a 300 ms pause, not one per
  // keystroke. Typing resets the page immediately; only the API call waits.
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Job | null>(null);
  const [stateAction, setStateAction] = useState<OpenState | null>(null);

  const jobsQuery = useJobs({ ...params, q: debouncedSearch || undefined });
  const directory = useRecruitmentDirectory();

  const jobs = jobsQuery.data?.status === 200 ? jobsQuery.data.data.items : [];
  const page = jobsQuery.data?.status === 200 ? jobsQuery.data.data.page : null;
  const filtersActive =
    searchInput.trim() !== "" || params.status !== undefined || params.department_id !== undefined;
  // `PageParams` clamps the requested page up to 1 but not down to the last
  // page. A mutation that empties the current page (closing the last open job
  // on page 2 of a filtered list) therefore returns page 2 of 1 with no rows.
  const pageOutOfRange = page !== null && page.page > Math.max(page.total_pages, 1);

  function change(next: Partial<ListJobsParams>) {
    // The q input goes through the debounce; selects commit directly.
    if ("q" in next) {
      setSearchInput(next.q ?? "");
      setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
      return;
    }
    setParams((current) => ({ ...current, ...next }));
  }

  function openCreate() {
    // A new job lands at the top of page 1, so return there before the dialog
    // opens (the leave/overtime create pattern).
    setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(job: Job) {
    setEditing(job);
    setFormOpen(true);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <JobsFilters
          params={{ ...params, q: searchInput }}
          departments={directory.departments}
          onChange={change}
        />
        <Button className="ml-auto" onClick={openCreate}>
          <Plus />
          {common("newJob")}
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {jobsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("loading")}</p>
          </div>
        ) : jobsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("loadError")}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void jobsQuery.refetch()}
                >
                  {common("retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : jobs.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Briefcase}
              message={
                filtersActive ? t("emptyFiltered") : t("empty")
              }
            />
            {pageOutOfRange ? (
              <div className="mt-3 flex justify-center">
                <Button variant="outline" size="sm" onClick={() => change({ page: 1 })}>
                  {common("backToFirstPage")}
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              {t("swipe")}
            </p>
            <Table
              aria-label={t("label")}
              containerLabel={t("containerLabel")}
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("columns.title")}</TableHead>
                  <TableHead>{t("columns.department")}</TableHead>
                  <TableHead>{t("columns.position")}</TableHead>
                  <TableHead>{t("columns.location")}</TableHead>
                  <TableHead className="text-right">{t("columns.openings")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  <TableHead>{t("columns.opened")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="pl-3 font-medium">{job.title}</TableCell>
                    <TableCell>
                      {organizationLabel(
                        directory.departmentNames,
                        job.department_id,
                        fallbacks("department", { id: job.department_id.slice(-4) }),
                      )}
                    </TableCell>
                    <TableCell>
                      {organizationLabel(
                        directory.positionNames,
                        job.position_id,
                        fallbacks("position", { id: job.position_id.slice(-4) }),
                      )}
                    </TableCell>
                    <TableCell>
                      {organizationLabel(
                        directory.locationNames,
                        job.location_id,
                        fallbacks("location", { id: job.location_id.slice(-4) }),
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {job.openings}
                    </TableCell>
                    <TableCell>
                      <Badge variant={jobStatusVariant[job.status]} data-status={job.status}>
                        {status(job.status)}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {job.opened_at === null || job.opened_at === undefined ? (
                        "—"
                      ) : (
                        <time dateTime={job.opened_at}>
                          {formatRecruitmentTimestamp(job.opened_at, locale)}
                        </time>
                      )}
                    </TableCell>
                    <TableCell className="pr-3 text-right">
                      <div className="flex justify-end gap-1">
                        {job.status === "closed" ? (
                          <span className="text-sm text-muted-foreground">—</span>
                        ) : (
                          <>
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={t("editAria", { title: job.title })}
                              onClick={() => openEdit(job)}
                            >
                              {common("edit")}
                            </Button>
                            {job.status === "draft" ? (
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={t("openAria", { title: job.title })}
                                onClick={() => setStateAction({ job, action: "open" })}
                              >
                                {common("open")}
                              </Button>
                            ) : (
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={t("closeAria", { title: job.title })}
                                onClick={() => setStateAction({ job, action: "close" })}
                              >
                                {common("close")}
                              </Button>
                            )}
                          </>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {page === null ? null : (
              <RecruitmentPagination
                page={page}
                label={t("paginationLabel")}
                onPageChange={(nextPage) => change({ page: nextPage })}
              />
            )}
          </>
        )}
      </Card>

      {formOpen ? (
        // Mount per open: `useForm` keeps values across a hidden Radix
        // Dialog, so a persistent instance would reopen a create form with the
        // previous job's values or a cancelled edit's unsaved changes.
        <JobFormDialog key={editing?.id ?? "new"} open onOpenChange={setFormOpen} job={editing} />
      ) : null}
      {stateAction === null ? null : (
        <JobStateDialog
          key={`${stateAction.job.id}-${stateAction.action}`}
          open
          onOpenChange={(open) => {
            if (!open) setStateAction(null);
          }}
          job={stateAction.job}
          action={stateAction.action}
        />
      )}
    </div>
  );
}
