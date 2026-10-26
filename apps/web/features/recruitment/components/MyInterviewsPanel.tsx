"use client";

/**
 * "My interviews" — the assigned interviewer's scoped view. Only rows whose
 * `interviewer_id` is the caller's employee link are listed (`mine=1`); the
 * candidate behind an interview gives the conversation its context, and the
 * assigned interviewer files the feedback that completes it. No management
 * actions (reschedule/cancel) are rendered here — the API is the authority.
 * Client leaf.
 */

import { CalendarClock, CircleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
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

import { formatRecruitmentTimestamp } from "../dates";
import {
  interviewStatusVariant,
  isForbiddenError,
  organizationLabel,
} from "../labels";
import {
  useInterviews,
  useRecruitmentCandidateDirectory,
  useRecruitmentJobDirectory,
} from "../hooks/use-recruitment";
import type { Interview, ListInterviewsParams } from "../types";
import { InterviewFeedbackDialog } from "./InterviewFeedbackDialog";
import { RecruitmentPagination } from "./RecruitmentPagination";

/** Page size for the assigned-interview list query. */
export const MY_INTERVIEWS_PAGE_SIZE = 20;

export function MyInterviewsPanel() {
  const t = useTranslations("recruitment.myInterviews");
  const common = useTranslations("recruitment.actions");
  const statusLabels = useTranslations("recruitment.status.interview");
  const fallbacks = useTranslations("recruitment.fallbacks");
  const locale = useLocale();
  const [params, setParams] = useState<ListInterviewsParams>({
    page: 1,
    page_size: MY_INTERVIEWS_PAGE_SIZE,
    mine: true,
  });
  const [feedback, setFeedback] = useState<Interview | null>(null);

  const interviewsQuery = useInterviews(params);
  const candidateDirectory = useRecruitmentCandidateDirectory();
  const jobDirectory = useRecruitmentJobDirectory();

  const interviews =
    interviewsQuery.data?.status === 200 ? interviewsQuery.data.data.items : [];
  const page = interviewsQuery.data?.status === 200 ? interviewsQuery.data.data.page : null;
  const pageOutOfRange = page !== null && page.page > Math.max(page.total_pages, 1);

  function candidateLabel(interview: Interview): string {
    return organizationLabel(
      candidateDirectory.candidateNames,
      interview.candidate_id,
      fallbacks("candidate", { id: interview.candidate_id.slice(-4) }),
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-medium">My interviews</h2>
        <p className="text-sm text-muted-foreground">
          Conversations assigned to you. File the feedback after the interview — the candidate&apos;s
          stage stays with HR.
        </p>
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
                <p>
                  {isForbiddenError(interviewsQuery.error)
                    ? "You don't have permission to view recruitment."
                    : "Could not load your interviews."}
                </p>
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
            <EmptyState
              icon={CalendarClock}
              message={t("empty")}
            />
            {pageOutOfRange ? (
              <div className="mt-3 flex justify-center">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setParams((current) => ({ ...current, page: 1 }))}
                >
                  Back to first page
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
                  <TableHead className="pl-3">{t("columns.candidate")}</TableHead>
                  <TableHead>{t("columns.job")}</TableHead>
                  <TableHead>{t("columns.scheduled")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  <TableHead>{t("columns.rating")}</TableHead>
                  <TableHead>{t("columns.feedback")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {interviews.map((interview) => {
                  const name = candidateLabel(interview);
                  return (
                    <TableRow
                      key={interview.id}
                      data-testid="my-interview-row"
                      data-status={interview.status}
                    >
                      <TableCell className="pl-3 font-medium">{name}</TableCell>
                      <TableCell>
                        {organizationLabel(
                          jobDirectory.jobNames,
                          interview.job_id,
                          fallbacks("job", { id: interview.job_id.slice(-4) }),
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
                      <TableCell className="max-w-72 text-sm text-muted-foreground">
                        {interview.feedback_notes === null ||
                        interview.feedback_notes === undefined ||
                        interview.feedback_notes === "" ? (
                          "—"
                        ) : (
                          <span className="line-clamp-2">{interview.feedback_notes}</span>
                        )}
                      </TableCell>
                      <TableCell className="pr-3 text-right">
                        {interview.status === "scheduled" ? (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={t("submitFeedbackAria", { name })}
                            onClick={() => setFeedback(interview)}
                          >
                            Submit feedback
                          </Button>
                        ) : (
                          <span className="text-sm text-muted-foreground">—</span>
                        )}
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
                onPageChange={(nextPage) => setParams((current) => ({ ...current, page: nextPage }))}
              />
            )}
          </>
        )}
      </Card>

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
    </div>
  );
}
