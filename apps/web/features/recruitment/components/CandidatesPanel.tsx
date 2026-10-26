"use client";

/**
 * Candidates tab — the pipeline HR runs. Filters narrow the list by job,
 * stage and name; row actions move a candidate one stage (`advance`), close it
 * (`reject` / `withdraw`) or hire it, which creates the Employee through the
 * API seam. Loading, empty, denied and error states are part of the surface;
 * a failed hire keeps the row `offered` and renders the mapped server sentence
 * in the dialog. Client leaf.
 */

import Link from "next/link";
import { CircleAlert, Plus, Search, Users } from "lucide-react";
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

import { useLocale, useTranslations } from "next-intl";

import { useDebouncedValue } from "@/hooks/use-debounced-value";

import { formatRecruitmentTimestamp } from "../dates";
import {
  candidateStatusVariant,
  isCandidateOpen,
  isForbiddenError,
  nextCandidateStage,
  organizationLabel,
} from "../labels";
import {
  useCandidateAdvance,
  useCandidates,
  useRecruitmentJobDirectory,
} from "../hooks/use-recruitment";
import type { Candidate, CandidateStatus, Job, ListCandidatesParams } from "../types";
import { CandidateDecisionDialog, type CandidateDecisionAction } from "./CandidateDecisionDialog";
import { CandidateFormDialog } from "./CandidateFormDialog";
import { HireCandidateDialog } from "./HireCandidateDialog";
import { RecruitmentPagination } from "./RecruitmentPagination";

const ANY = "any";

/** Page size for the candidates list query. */
export const CANDIDATES_PAGE_SIZE = 20;

const STATUS_ORDER: CandidateStatus[] = [
  "applied",
  "interviewing",
  "offered",
  "hired",
  "rejected",
  "withdrawn",
];

/**
 * Filter bar. Clearing a filter drops the parameter (`undefined`), never
 * sends `null`: the generated client serializes null as the literal "null".
 */
function CandidateFilters({
  params,
  jobs,
  onChange,
}: {
  params: ListCandidatesParams;
  jobs: Job[];
  onChange: (next: Partial<ListCandidatesParams>) => void;
}) {
  const t = useTranslations("recruitment.candidates.filters");
  const statusLabels = useTranslations("recruitment.status.candidate");

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="candidate-filters">
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
        value={params.job_id ?? ANY}
        onValueChange={(value) => onChange({ job_id: value === ANY ? undefined : value, page: 1 })}
      >
        <SelectTrigger aria-label={t("jobAria")} className="w-full sm:w-56">
          <SelectValue placeholder={t("allJobs")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("allJobs")}</SelectItem>
          {jobs.map((job) => (
            <SelectItem key={job.id} value={job.id}>
              {job.title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={params.status ?? ANY}
        onValueChange={(value) =>
          onChange({
            status: value === ANY ? undefined : (value as CandidateStatus),
            page: 1,
          })
        }
      >
        <SelectTrigger aria-label={t("statusAria")} className="w-full sm:w-44">
          <SelectValue placeholder={t("allStatuses")} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{t("allStatuses")}</SelectItem>
          {STATUS_ORDER.map((status) => (
            <SelectItem key={status} value={status}>
              {statusLabels(status)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function candidateName(candidate: Candidate): string {
  return `${candidate.first_name} ${candidate.last_name}`;
}

type DecisionState = { candidate: Candidate; action: CandidateDecisionAction };

export function CandidatesPanel() {
  const t = useTranslations("recruitment.candidates");
  const common = useTranslations("recruitment.actions");
  const status = useTranslations("recruitment.status.candidate");
  const sources = useTranslations("recruitment.sources");
  const fallbacks = useTranslations("recruitment.fallbacks");
  const locale = useLocale();
  const errors = useTranslations("recruitment.errors");
  const [params, setParams] = useState<ListCandidatesParams>({
    page: 1,
    page_size: CANDIDATES_PAGE_SIZE,
  });
  // Typed search feeds the query through the 300 ms debounce; the page resets
  // immediately so the next request is page one.
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Candidate | null>(null);
  const [decision, setDecision] = useState<DecisionState | null>(null);
  const [hiring, setHiring] = useState<Candidate | null>(null);

  const candidatesQuery = useCandidates({ ...params, q: debouncedSearch || undefined });
  const directory = useRecruitmentJobDirectory();
  const advanceCandidate = useCandidateAdvance();

  const candidates = candidatesQuery.data?.status === 200 ? candidatesQuery.data.data.items : [];
  const page = candidatesQuery.data?.status === 200 ? candidatesQuery.data.data.page : null;
  const filtersActive =
    searchInput.trim() !== "" || params.status !== undefined || params.job_id !== undefined;
  // `PageParams` clamps up but not down: a mutation that empties the last page
  // can leave the list on page 2 of 1.
  const pageOutOfRange = page !== null && page.page > Math.max(page.total_pages, 1);

  function change(next: Partial<ListCandidatesParams>) {
    if ("q" in next) {
      setSearchInput(next.q ?? "");
      setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
      return;
    }
    setParams((current) => ({ ...current, ...next }));
  }

  function openCreate() {
    setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(candidate: Candidate) {
    setEditing(candidate);
    setFormOpen(true);
  }

  function emptyMessage(): string {
    if (params.job_id !== undefined && params.status === undefined && searchInput.trim() === "") {
      return t("emptyForJob");
    }
    if (filtersActive) {
      return t("emptyFiltered");
    }
    return t("empty");
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <CandidateFilters
          params={{ ...params, q: searchInput }}
          jobs={directory.jobs}
          onChange={change}
        />
        <Button className="ml-auto" onClick={openCreate}>
          <Plus />
          {common("newCandidate")}
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {candidatesQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("loading")}</p>
          </div>
        ) : candidatesQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>
                  {isForbiddenError(candidatesQuery.error)
                    ? errors("denied")
                    : t("loadError")}
                </p>
                {isForbiddenError(candidatesQuery.error) ? null : (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void candidatesQuery.refetch()}
                  >
                    {common("retry")}
                  </Button>
                )}
              </AlertDescription>
            </Alert>
          </div>
        ) : candidates.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={Users} message={emptyMessage()} />
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
                  <TableHead className="pl-3">{t("columns.name")}</TableHead>
                  <TableHead>{t("columns.email")}</TableHead>
                  <TableHead>{t("columns.job")}</TableHead>
                  <TableHead>{t("columns.source")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  <TableHead>{t("columns.applied")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {candidates.map((candidate) => {
                  const name = candidateName(candidate);
                  const next = nextCandidateStage(candidate.status);
                  const open = isCandidateOpen(candidate.status);
                  // A closed job accepts no new hires; an in-flight offered
                  // candidate can only be rejected, so do not offer an action
                  // that can only return `409 job_not_open`.
                  const jobClosed =
                    directory.jobs.find((job) => job.id === candidate.job_id)?.status === "closed";
                  return (
                    <TableRow key={candidate.id}>
                      <TableCell className="pl-3 font-medium">{name}</TableCell>
                      <TableCell className="text-muted-foreground">{candidate.email}</TableCell>
                      <TableCell>
                        {organizationLabel(
                          directory.jobNames,
                          candidate.job_id,
                          fallbacks("job", { id: candidate.job_id.slice(-4) }),
                        )}
                      </TableCell>
                      <TableCell>{sources(candidate.source)}</TableCell>
                      <TableCell>
                        <Badge
                          variant={candidateStatusVariant[candidate.status]}
                          data-status={candidate.status}
                        >
                          {status(candidate.status)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        <time dateTime={candidate.created_at}>
                          {formatRecruitmentTimestamp(candidate.created_at, locale)}
                        </time>
                      </TableCell>
                      <TableCell className="pr-3 text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={t("editAria", { name })}
                            onClick={() => openEdit(candidate)}
                          >
                            {common("edit")}
                          </Button>
                          {next === null ? null : (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={t("advanceAria", { name })}
                              disabled={advanceCandidate.isPending}
                              onClick={() => advanceCandidate.mutate({ candidateId: candidate.id })}
                            >
                              {common("advance")}
                            </Button>
                          )}
                          {open ? (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={t("rejectAria", { name })}
                              onClick={() => setDecision({ candidate, action: "reject" })}
                            >
                              {common("reject")}
                            </Button>
                          ) : null}
                          {open ? (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={t("withdrawAria", { name })}
                              onClick={() => setDecision({ candidate, action: "withdraw" })}
                            >
                              {common("withdraw")}
                            </Button>
                          ) : null}
                          {candidate.status === "offered" && !jobClosed ? (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={t("hireAria", { name })}
                              onClick={() => setHiring(candidate)}
                            >
                              {common("hire")}
                            </Button>
                          ) : null}
                          {candidate.employee_id === null ||
                          candidate.employee_id === undefined ? null : (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              asChild
                            >
                              <Link
                                aria-label={t("viewEmployeeAria", { name })}
                                href={`/employees/${candidate.employee_id}`}
                              >
                                {common("viewEmployee")}
                              </Link>
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
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
        // Mount per open: RHF keeps values across a hidden Radix Dialog.
        <CandidateFormDialog
          key={editing?.id ?? "new"}
          open
          onOpenChange={setFormOpen}
          candidate={editing}
          openJobs={directory.openJobs}
          initialJobId={params.job_id ?? undefined}
          jobsPending={directory.isPending}
          jobTitle={
            editing === null
              ? undefined
              : organizationLabel(
                directory.jobNames,
                editing.job_id,
                fallbacks("job", { id: editing.job_id.slice(-4) }),
              )
          }
        />
      ) : null}
      {decision === null ? null : (
        <CandidateDecisionDialog
          key={`${decision.candidate.id}-${decision.action}`}
          open
          onOpenChange={(open) => {
            if (!open) setDecision(null);
          }}
          candidate={decision.candidate}
          action={decision.action}
        />
      )}
      {hiring === null ? null : (
        <HireCandidateDialog
          key={hiring.id}
          open
          onOpenChange={(open) => {
            if (!open) setHiring(null);
          }}
          candidate={hiring}
          jobTitle={organizationLabel(
            directory.jobNames,
            hiring.job_id,
            fallbacks("job", { id: hiring.job_id.slice(-4) }),
          )}
        />
      )}
    </div>
  );
}
