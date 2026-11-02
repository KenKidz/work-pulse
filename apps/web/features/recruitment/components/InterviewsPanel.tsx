"use client";

/**
 * Interviews tab — HR schedules a conversation on an interviewing candidate,
 * reschedules/re-assigns or cancels while it is `scheduled`, and the assigned
 * interviewer (when that is the signed-in user) files the feedback that
 * completes it. Loading, empty, denied and error states are part of the
 * surface; pinned `409`/`404` codes render as mapped sentences. Client leaf.
 */

import { CalendarClock, CircleAlert, Plus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
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

import { useApiErrorTranslator } from "@/lib/api-errors";

import { formatRecruitmentTimestamp } from "../dates";
import { interviewStatusVariant, isForbiddenError, organizationLabel } from "../labels";
import {
  useActiveEmployees,
  useInterviews,
  useRecruitmentCandidateDirectory,
  useRecruitmentJobDirectory,
} from "../hooks/use-recruitment";
import type { Interview, InterviewStatus, ListInterviewsParams } from "../types";
import { InterviewCancelDialog } from "./InterviewCancelDialog";
import { InterviewFeedbackDialog } from "./InterviewFeedbackDialog";
import { InterviewFormDialog } from "./InterviewFormDialog";
import { RecruitmentPagination } from "./RecruitmentPagination";

const ANY = "any";

/** Page size for the interviews list query. */
export const INTERVIEWS_PAGE_SIZE = 20;

const STATUS_ORDER: InterviewStatus[] = ["scheduled", "completed", "cancelled"];

export function InterviewsPanel() {
  const t = useTranslations("recruitment.interviews");
  const common = useTranslations("recruitment.actions");
  const statusLabels = useTranslations("recruitment.status.interview");
  const fallbacks = useTranslations("recruitment.fallbacks");
  const locale = useLocale();
  const errors = useApiErrorTranslator("recruitment.errors");
  const { user } = useAuth();
  const [params, setParams] = useState<ListInterviewsParams>({
    page: 1,
    page_size: INTERVIEWS_PAGE_SIZE,
  });
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Interview | null>(null);
  const [feedback, setFeedback] = useState<Interview | null>(null);
  const [cancelling, setCancelling] = useState<Interview | null>(null);

  const interviewsQuery = useInterviews(params);
  const candidateDirectory = useRecruitmentCandidateDirectory();
  const jobDirectory = useRecruitmentJobDirectory();
  const interviewers = useActiveEmployees();

  const interviews = interviewsQuery.data?.status === 200 ? interviewsQuery.data.data.items : [];
  const page = interviewsQuery.data?.status === 200 ? interviewsQuery.data.data.page : null;
  const filtersActive = params.status !== undefined || params.job_id !== undefined;
  const pageOutOfRange = page !== null && page.page > Math.max(page.total_pages, 1);

  const interviewerNames = new Map(
    interviewers.map((employee) => [employee.id, `${employee.first_name} ${employee.last_name}`]),
  );

  function change(next: Partial<ListInterviewsParams>) {
    setParams((current) => ({ ...current, ...next }));
  }

  function openSchedule() {
    setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(interview: Interview) {
    setEditing(interview);
    setFormOpen(true);
  }

  function candidateLabel(interview: Interview): string {
    return organizationLabel(
      candidateDirectory.candidateNames,
      interview.candidate_id,
      fallbacks("candidate", { id: interview.candidate_id.slice(-4) }),
    );
  }

  function emptyMessage(): string {
    return filtersActive ? t("emptyFiltered") : t("empty");
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2" data-testid="interview-filters">
        <Select
          value={params.status ?? ANY}
          onValueChange={(value) =>
            change({
              status: value === ANY ? undefined : (value as InterviewStatus),
              page: 1,
            })
          }
        >
          <SelectTrigger aria-label={t("filters.statusAria")} className="w-full sm:w-44">
            <SelectValue placeholder={t("filters.allStatuses")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("filters.allStatuses")}</SelectItem>
            {STATUS_ORDER.map((status) => (
              <SelectItem key={status} value={status}>
                {statusLabels(status)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={params.job_id ?? ANY}
          onValueChange={(value) => change({ job_id: value === ANY ? undefined : value, page: 1 })}
        >
          <SelectTrigger aria-label={t("filters.jobAria")} className="w-full sm:w-56">
            <SelectValue placeholder={t("filters.allJobs")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("filters.allJobs")}</SelectItem>
            {jobDirectory.jobs.map((job) => (
              <SelectItem key={job.id} value={job.id}>
                {job.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Button className="ml-auto" onClick={openSchedule}>
          <Plus />
          {common("schedule")}
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {interviewsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("loading")}</p>
          </div>
        ) : interviewsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{isForbiddenError(interviewsQuery.error) ? errors("denied") : t("loadError")}</p>
                {isForbiddenError(interviewsQuery.error) ? null : (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void interviewsQuery.refetch()}
                  >
                    {common("retry")}
                  </Button>
                )}
              </AlertDescription>
            </Alert>
          </div>
        ) : interviews.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={CalendarClock} message={emptyMessage()} />
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
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">{t("swipe")}</p>
            <Table aria-label={t("label")} containerLabel={t("containerLabel")}>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("columns.candidate")}</TableHead>
                  <TableHead>{t("columns.job")}</TableHead>
                  <TableHead>{t("columns.interviewer")}</TableHead>
                  <TableHead>{t("columns.scheduled")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  <TableHead>{t("columns.rating")}</TableHead>
                  <TableHead>{t("columns.notes")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {interviews.map((interview) => {
                  const name = candidateLabel(interview);
                  // Completed rows show the interviewer's feedback; when the
                  // feedback notes are empty, keep the scheduling context.
                  const notes =
                    interview.status === "completed"
                      ? (interview.feedback_notes ?? interview.notes)
                      : interview.notes;
                  return (
                    <TableRow key={interview.id} data-testid="interview-row">
                      <TableCell className="pl-3 font-medium">{name}</TableCell>
                      <TableCell>
                        {organizationLabel(jobDirectory.jobNames, interview.job_id, "Job")}
                      </TableCell>
                      <TableCell>
                        {organizationLabel(
                          interviewerNames,
                          interview.interviewer_id,
                          fallbacks("employee", { id: interview.interviewer_id.slice(-4) }),
                        )}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        <time dateTime={interview.scheduled_at}>
                          {formatRecruitmentTimestamp(interview.scheduled_at, locale)}
                        </time>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={interviewStatusVariant[interview.status]}
                          data-status={interview.status}
                        >
                          {statusLabels(interview.status)}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono tabular-nums">
                        {interview.rating === null || interview.rating === undefined
                          ? "—"
                          : `${interview.rating} / 5`}
                      </TableCell>
                      <TableCell className="max-w-56 text-sm text-muted-foreground">
                        {notes === null || notes === undefined || notes === "" ? (
                          "—"
                        ) : (
                          <span className="line-clamp-2">{notes}</span>
                        )}
                      </TableCell>
                      <TableCell className="pr-3 text-right">
                        <div className="flex justify-end gap-1">
                          {interview.status !== "scheduled" ? (
                            <span className="text-sm text-muted-foreground">—</span>
                          ) : (
                            <>
                              {user.employee_id === interview.interviewer_id ? (
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="min-h-11 sm:min-h-7"
                                  aria-label={t("submitFeedbackAria", { name })}
                                  onClick={() => setFeedback(interview)}
                                >
                                  {common("feedback")}
                                </Button>
                              ) : null}
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={t("rescheduleAria", { name })}
                                onClick={() => openEdit(interview)}
                              >
                                {common("reschedule")}
                              </Button>
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={t("cancelAria", { name })}
                                onClick={() => setCancelling(interview)}
                              >
                                {common("cancel")}
                              </Button>
                            </>
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
        <InterviewFormDialog
          key={editing?.id ?? "new"}
          open
          onOpenChange={setFormOpen}
          interview={editing}
          candidates={candidateDirectory.interviewingCandidates}
          candidatesPending={candidateDirectory.isPending}
          candidateName={
            editing === null
              ? undefined
              : organizationLabel(
                  candidateDirectory.candidateNames,
                  editing.candidate_id,
                  "Candidate",
                )
          }
        />
      ) : null}
      {feedback === null ? null : (
        <InterviewFeedbackDialog
          key={feedback.id}
          open
          onOpenChange={(open) => {
            if (!open) setFeedback(null);
          }}
          interview={feedback}
          candidateName={candidateLabel(feedback)}
        />
      )}
      {cancelling === null ? null : (
        <InterviewCancelDialog
          key={cancelling.id}
          open
          onOpenChange={(open) => {
            if (!open) setCancelling(null);
          }}
          interview={cancelling}
          candidateName={candidateLabel(cancelling)}
        />
      )}
    </div>
  );
}
