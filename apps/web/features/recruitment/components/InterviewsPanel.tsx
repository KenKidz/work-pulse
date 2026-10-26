"use client";

/**
 * Interviews tab — HR schedules a conversation on an interviewing candidate,
 * reschedules/re-assigns or cancels while it is `scheduled`, and the assigned
 * interviewer (when that is the signed-in user) files the feedback that
 * completes it. Loading, empty, denied and error states are part of the
 * surface; pinned `409`/`404` codes render as mapped sentences. Client leaf.
 */

import { CalendarClock, CircleAlert, Plus } from "lucide-react";
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

import { formatRecruitmentTimestamp } from "../dates";
import {
  interviewStatusLabel,
  interviewStatusVariant,
  isForbiddenError,
  organizationLabel,
} from "../labels";
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

  const interviews =
    interviewsQuery.data?.status === 200 ? interviewsQuery.data.data.items : [];
  const page = interviewsQuery.data?.status === 200 ? interviewsQuery.data.data.page : null;
  const filtersActive = params.status !== undefined || params.job_id !== undefined;
  const pageOutOfRange = page !== null && page.page > Math.max(page.total_pages, 1);

  const interviewerNames = new Map(
    interviewers.map((employee) => [
      employee.id,
      `${employee.first_name} ${employee.last_name}`,
    ]),
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
    return organizationLabel(candidateDirectory.candidateNames, interview.candidate_id, "Candidate");
  }

  function emptyMessage(): string {
    return filtersActive
      ? "No interviews match these filters. Clear a filter to see the full schedule."
      : "No interviews yet. Schedule one for an interviewing candidate.";
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
          <SelectTrigger aria-label="Filter by status" className="w-full sm:w-44">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All statuses</SelectItem>
            {STATUS_ORDER.map((status) => (
              <SelectItem key={status} value={status}>
                {interviewStatusLabel[status]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={params.job_id ?? ANY}
          onValueChange={(value) =>
            change({ job_id: value === ANY ? undefined : value, page: 1 })
          }
        >
          <SelectTrigger aria-label="Filter by job" className="w-full sm:w-56">
            <SelectValue placeholder="All jobs" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All jobs</SelectItem>
            {jobDirectory.jobs.map((job) => (
              <SelectItem key={job.id} value={job.id}>
                {job.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Button className="ml-auto" onClick={openSchedule}>
          <Plus />
          Schedule interview
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {interviewsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading interviews…</p>
          </div>
        ) : interviewsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>
                  {isForbiddenError(interviewsQuery.error)
                    ? "You don't have permission to manage recruitment."
                    : "Could not load interviews."}
                </p>
                {isForbiddenError(interviewsQuery.error) ? null : (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void interviewsQuery.refetch()}
                  >
                    Retry
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
                  Back to first page
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every interview field.
            </p>
            <Table
              aria-label="Interviews"
              containerLabel="Interviews. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Candidate</TableHead>
                  <TableHead>Job</TableHead>
                  <TableHead>Interviewer</TableHead>
                  <TableHead>Scheduled</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Rating</TableHead>
                  <TableHead>Notes</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
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
                        {organizationLabel(
                          jobDirectory.jobNames,
                          interview.job_id,
                          "Job",
                        )}
                      </TableCell>
                      <TableCell>
                        {organizationLabel(
                          interviewerNames,
                          interview.interviewer_id,
                          "Employee",
                        )}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        <time dateTime={interview.scheduled_at}>
                          {formatRecruitmentTimestamp(interview.scheduled_at)}
                        </time>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={interviewStatusVariant[interview.status]}
                          data-status={interview.status}
                        >
                          {interviewStatusLabel[interview.status]}
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
                                  aria-label={`Submit feedback for ${name}`}
                                  onClick={() => setFeedback(interview)}
                                >
                                  Feedback
                                </Button>
                              ) : null}
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={`Reschedule interview with ${name}`}
                                onClick={() => openEdit(interview)}
                              >
                                Reschedule
                              </Button>
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={`Cancel interview with ${name}`}
                                onClick={() => setCancelling(interview)}
                              >
                                Cancel
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
                label="interviews"
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
