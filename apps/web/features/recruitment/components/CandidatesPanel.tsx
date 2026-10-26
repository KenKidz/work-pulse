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

import { useDebouncedValue } from "@/hooks/use-debounced-value";

import { formatRecruitmentTimestamp } from "../dates";
import {
  candidateSourceLabel,
  candidateStatusLabel,
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
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="candidate-filters">
      <div className="relative w-full sm:w-64">
        <Search className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label="Search candidates"
          placeholder="Search by name"
          className="w-full pl-8"
          value={params.q ?? ""}
          onChange={(event) => onChange({ q: event.target.value || undefined })}
        />
      </div>

      <Select
        value={params.job_id ?? ANY}
        onValueChange={(value) => onChange({ job_id: value === ANY ? undefined : value, page: 1 })}
      >
        <SelectTrigger aria-label="Filter by job" className="w-full sm:w-56">
          <SelectValue placeholder="All jobs" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All jobs</SelectItem>
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
        <SelectTrigger aria-label="Filter by status" className="w-full sm:w-44">
          <SelectValue placeholder="All statuses" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All statuses</SelectItem>
          {STATUS_ORDER.map((status) => (
            <SelectItem key={status} value={status}>
              {candidateStatusLabel[status]}
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
      return "No candidates for this job yet. Add a candidate to start the pipeline.";
    }
    if (filtersActive) {
      return "No candidates match these filters. Clear a filter or search for another name.";
    }
    return "No candidates yet. Add a candidate to an open job to start the pipeline.";
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
          New candidate
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {candidatesQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading candidates…</p>
          </div>
        ) : candidatesQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>
                  {isForbiddenError(candidatesQuery.error)
                    ? "You don't have permission to manage recruitment."
                    : "Could not load candidates."}
                </p>
                {isForbiddenError(candidatesQuery.error) ? null : (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void candidatesQuery.refetch()}
                  >
                    Retry
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
                  Back to first page
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every candidate field.
            </p>
            <Table
              aria-label="Candidates"
              containerLabel="Candidates. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Job</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Applied</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
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
                        {organizationLabel(directory.jobNames, candidate.job_id, "Job")}
                      </TableCell>
                      <TableCell>{candidateSourceLabel[candidate.source]}</TableCell>
                      <TableCell>
                        <Badge
                          variant={candidateStatusVariant[candidate.status]}
                          data-status={candidate.status}
                        >
                          {candidateStatusLabel[candidate.status]}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        <time dateTime={candidate.created_at}>
                          {formatRecruitmentTimestamp(candidate.created_at)}
                        </time>
                      </TableCell>
                      <TableCell className="pr-3 text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={`Edit ${name}`}
                            onClick={() => openEdit(candidate)}
                          >
                            Edit
                          </Button>
                          {next === null ? null : (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Advance ${name}`}
                              disabled={advanceCandidate.isPending}
                              onClick={() => advanceCandidate.mutate({ candidateId: candidate.id })}
                            >
                              Advance
                            </Button>
                          )}
                          {open ? (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Reject ${name}`}
                              onClick={() => setDecision({ candidate, action: "reject" })}
                            >
                              Reject
                            </Button>
                          ) : null}
                          {open ? (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Withdraw ${name}`}
                              onClick={() => setDecision({ candidate, action: "withdraw" })}
                            >
                              Withdraw
                            </Button>
                          ) : null}
                          {candidate.status === "offered" && !jobClosed ? (
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={`Hire ${name}`}
                              onClick={() => setHiring(candidate)}
                            >
                              Hire
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
                                aria-label={`View employee ${name}`}
                                href={`/employees/${candidate.employee_id}`}
                              >
                                Employee
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
                label="candidates"
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
              : organizationLabel(directory.jobNames, editing.job_id, "Job")
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
          jobTitle={organizationLabel(directory.jobNames, hiring.job_id, "Job")}
        />
      )}
    </div>
  );
}
