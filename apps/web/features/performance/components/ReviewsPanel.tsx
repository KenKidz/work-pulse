"use client";

/**
 * Reviews tab — three surfaces in one place:
 *
 * - the author/management workspace (filters, create, draft edit, complete,
 *   cancel) and the subject's read-only "My reviews" (the API narrows to
 *   completed; the surface filters defensively too);
 * - "Feedback requested", the nominated provider's `feedback_for_me` worklist,
 *   with one immutable submission per review;
 * - the detail dialog, which appends feedback only for a viewer with standing
 *   who is not the subject — the employee's tree never contains peer text.
 *
 * Client leaf.
 */

import { CircleAlert, ClipboardCheck } from "lucide-react";
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

import { formatPerformanceTimestamp } from "../dates";
import { employeeLabel, isForbiddenError, reviewStatusVariant } from "../labels";
import {
  usePerformanceAccess,
  usePerformanceDirectory,
  useReviews,
} from "../hooks/use-performance";
import type { ListReviewsParams, Review, ReviewStatus } from "../types";
import { FeedbackDialog } from "./FeedbackDialog";
import { PerformanceDeniedState } from "./PerformanceDeniedState";
import { PerformancePagination } from "./PerformancePagination";
import { ReviewDetailDialog } from "./ReviewDetailDialog";
import { ReviewFormDialog } from "./ReviewFormDialog";
import { ReviewStateDialog, type ReviewStateAction } from "./ReviewStateDialog";

const ANY = "any";
const REVIEWS_PAGE_SIZE = 20;
const WORKLIST_PAGE_SIZE = 10;

const REVIEW_STATUSES: ReviewStatus[] = ["draft", "completed", "cancelled"];

type OpenState = { review: Review; action: ReviewStateAction };

export function ReviewsPanel() {
  const t = useTranslations("performance.reviews");
  const worklistT = useTranslations("performance.feedback.worklist");
  const common = useTranslations("performance.actions");
  const status = useTranslations("performance.status.review");
  const fallbacks = useTranslations("performance.fallbacks");
  const locale = useLocale();
  const { user, canViewTeam, canRunReviews, canViewFeedback } = usePerformanceAccess();
  const directory = usePerformanceDirectory();
  const [params, setParams] = useState<ListReviewsParams>({
    page: 1,
    page_size: REVIEWS_PAGE_SIZE,
  });
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Review | null>(null);
  const [stateAction, setStateAction] = useState<OpenState | null>(null);
  const [detail, setDetail] = useState<Review | null>(null);
  const [feedbackFor, setFeedbackFor] = useState<Review | null>(null);
  const [worklistPage, setWorklistPage] = useState(1);

  const reviewsQuery = useReviews(params);
  const worklistQuery = useReviews(
    { feedback_for_me: true, page: worklistPage, page_size: WORKLIST_PAGE_SIZE },
    { query: { enabled: user.employee_id != null } },
  );

  const rawReviews = reviewsQuery.data?.status === 200 ? reviewsQuery.data.data.items : [];
  // The API already narrows a subject to completed rows; filtering here keeps
  // a draft out of the employee's tree even if a payload ever regresses.
  const reviews = canViewTeam
    ? rawReviews
    : rawReviews.filter((review) => review.status === "completed");
  const pageInfo = reviewsQuery.data?.status === 200 ? reviewsQuery.data.data.page : null;
  const filtersActive = params.status !== undefined || params.employee_id !== undefined;
  // `PageParams` clamps a requested page up to 1 but not down to the last
  // page: a mutation that empties page 2 leaves it out of range.
  const pageOutOfRange = pageInfo !== null && pageInfo.page > Math.max(pageInfo.total_pages, 1);

  const worklistItems =
    user.employee_id === null || worklistQuery.data?.status !== 200
      ? []
      : worklistQuery.data.data.items.filter((review) => review.status === "draft");
  const worklistPageInfo = worklistQuery.data?.status === 200 ? worklistQuery.data.data.page : null;
  const worklistOutOfRange =
    worklistPageInfo !== null && worklistPageInfo.page > Math.max(worklistPageInfo.total_pages, 1);
  const showWorklist =
    user.employee_id !== null && (worklistItems.length > 0 || worklistOutOfRange);

  function employeeName(review: Review): string {
    return employeeLabel(
      directory.nameById,
      review.employee_id,
      fallbacks("employee", { id: review.employee_id.slice(-4) }),
    );
  }

  /** Unique per row: one employee can have several ad-hoc reviews. */
  function reviewLabel(review: Review): string {
    const name = employeeName(review);
    return review.period === null || review.period === undefined
      ? name
      : `${name} · ${review.period}`;
  }

  function change(next: Partial<ListReviewsParams>) {
    setParams((current) => ({ ...current, ...next }));
  }

  function openCreate() {
    setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(review: Review) {
    setEditing(review);
    setFormOpen(true);
  }

  const isSubjectOf = (review: Review): boolean =>
    user.employee_id !== null && user.employee_id === review.employee_id;

  return (
    <div className="space-y-6">
      {!showWorklist ? null : (
        <section aria-labelledby="performance-feedback-worklist-title" className="space-y-3">
          <div className="space-y-1">
            <h2 id="performance-feedback-worklist-title" className="text-base font-medium">
              {worklistT("title")}
            </h2>
            <p className="max-w-2xl text-sm text-pretty text-muted-foreground">
              {worklistT("description")}
            </p>
          </div>
          <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
            {worklistItems.length === 0 ? (
              <div className="p-4">
                <EmptyState icon={ClipboardCheck} message={worklistT("empty")} />
                <div className="mt-3 flex justify-center">
                  <Button variant="outline" size="sm" onClick={() => setWorklistPage(1)}>
                    {common("backToFirstPage")}
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
                  {worklistT("swipe")}
                </p>
                <Table aria-label={worklistT("label")} containerLabel={worklistT("containerLabel")}>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="pl-3">{worklistT("columns.employee")}</TableHead>
                      <TableHead>{worklistT("columns.status")}</TableHead>
                      <TableHead className="pr-3 text-right">
                        {worklistT("columns.actions")}
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {worklistItems.map((review) => {
                      const name = employeeName(review);
                      const label = reviewLabel(review);
                      return (
                        <TableRow key={review.id}>
                          <TableCell className="pl-3 font-medium">{name}</TableCell>
                          <TableCell>
                            <Badge
                              variant={reviewStatusVariant[review.status]}
                              data-status={review.status}
                            >
                              {status(review.status)}
                            </Badge>
                          </TableCell>
                          <TableCell className="pr-3 text-right">
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={worklistT("submitAria", { name: label })}
                              onClick={() => setFeedbackFor(review)}
                            >
                              {common("giveFeedback")}
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
                {worklistPageInfo === null ? null : (
                  <PerformancePagination
                    page={worklistPageInfo}
                    label={worklistT("paginationLabel")}
                    onPageChange={setWorklistPage}
                  />
                )}
              </>
            )}
          </Card>
        </section>
      )}

      <section aria-labelledby="performance-reviews-title" className="space-y-4">
        <div className="space-y-1">
          <h2 id="performance-reviews-title" className="text-lg font-semibold">
            {canViewTeam ? t("title") : t("myTitle")}
          </h2>
          <p className="max-w-2xl text-sm text-pretty text-muted-foreground">
            {canViewTeam ? t("description") : t("myDescription")}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canViewTeam ? (
            <Select
              value={params.status ?? ANY}
              onValueChange={(value) =>
                change({
                  status: value === ANY ? undefined : (value as ReviewStatus),
                  page: 1,
                })
              }
            >
              <SelectTrigger aria-label={t("filters.statusAria")} className="w-full sm:w-40">
                <SelectValue placeholder={t("filters.anyStatus")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>{t("filters.anyStatus")}</SelectItem>
                {REVIEW_STATUSES.map((reviewStatus) => (
                  <SelectItem key={reviewStatus} value={reviewStatus}>
                    {status(reviewStatus)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}

          {canViewTeam ? (
            <Select
              value={params.employee_id ?? ANY}
              onValueChange={(value) =>
                change({
                  employee_id: value === ANY ? undefined : value,
                  page: 1,
                })
              }
            >
              <SelectTrigger aria-label={t("filters.employeeAria")} className="w-full sm:w-56">
                <SelectValue placeholder={t("filters.allEmployees")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>{t("filters.allEmployees")}</SelectItem>
                {directory.filterEmployees.map((employee) => (
                  <SelectItem key={employee.id} value={employee.id}>
                    {`${employee.first_name} ${employee.last_name}`.trim()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}

          {canRunReviews ? (
            <Button className="ml-auto" onClick={openCreate}>
              {common("newReview")}
            </Button>
          ) : null}
        </div>

        <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
          {reviewsQuery.isPending ? (
            <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
              <Spinner className="size-4" />
              <p>{t("loading")}</p>
            </div>
          ) : reviewsQuery.isError ? (
            isForbiddenError(reviewsQuery.error) ? (
              <div className="p-4">
                <PerformanceDeniedState message={t("denied")} />
              </div>
            ) : (
              <div className="p-4">
                <Alert variant="destructive">
                  <CircleAlert aria-hidden="true" />
                  <AlertDescription>
                    <p>{t("loadError")}</p>
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-2"
                      onClick={() => void reviewsQuery.refetch()}
                    >
                      {common("retry")}
                    </Button>
                  </AlertDescription>
                </Alert>
              </div>
            )
          ) : reviews.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={ClipboardCheck}
                message={
                  canViewTeam && filtersActive
                    ? t("emptyFiltered")
                    : canViewTeam
                      ? t("empty")
                      : t("emptyMine")
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
              <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">{t("swipe")}</p>
              <Table aria-label={t("label")} containerLabel={t("containerLabel")}>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    {canViewTeam ? (
                      <TableHead className="pl-3">{t("columns.employee")}</TableHead>
                    ) : null}
                    <TableHead className={canViewTeam ? undefined : "pl-3"}>
                      {t("columns.period")}
                    </TableHead>
                    <TableHead className="text-right">{t("columns.rating")}</TableHead>
                    <TableHead>{t("columns.status")}</TableHead>
                    <TableHead>{t("columns.completed")}</TableHead>
                    <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {reviews.map((review) => {
                    const name = employeeName(review);
                    const label = reviewLabel(review);
                    const rating = review.rating;
                    return (
                      <TableRow key={review.id}>
                        {canViewTeam ? (
                          <TableCell className="pl-3 font-medium">{name}</TableCell>
                        ) : null}
                        <TableCell className={canViewTeam ? undefined : "pl-3"}>
                          {review.period ?? "—"}
                        </TableCell>
                        <TableCell className="text-right">
                          {rating === null || rating === undefined ? (
                            <span className="text-muted-foreground">—</span>
                          ) : (
                            <span
                              className="font-mono tabular-nums"
                              aria-label={t("ratingValue", { rating })}
                            >
                              {rating}
                            </span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={reviewStatusVariant[review.status]}
                            data-status={review.status}
                          >
                            {status(review.status)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {review.completed_at === null || review.completed_at === undefined ? (
                            "—"
                          ) : (
                            <time dateTime={review.completed_at}>
                              {formatPerformanceTimestamp(review.completed_at, locale)}
                            </time>
                          )}
                        </TableCell>
                        <TableCell className="pr-3 text-right">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="xs"
                              className="min-h-11 sm:min-h-7"
                              aria-label={t("viewAria", { name: label })}
                              onClick={() => setDetail(review)}
                            >
                              {common("view")}
                            </Button>
                            {canRunReviews && review.status === "draft" ? (
                              <>
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="min-h-11 sm:min-h-7"
                                  aria-label={t("editAria", { name: label })}
                                  onClick={() => openEdit(review)}
                                >
                                  {common("edit")}
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="min-h-11 sm:min-h-7"
                                  aria-label={t("completeAria", { name: label })}
                                  onClick={() => setStateAction({ review, action: "complete" })}
                                >
                                  {common("complete")}
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="min-h-11 sm:min-h-7"
                                  aria-label={t("cancelAria", { name: label })}
                                  onClick={() => setStateAction({ review, action: "cancel" })}
                                >
                                  {common("cancel")}
                                </Button>
                              </>
                            ) : null}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              {pageInfo === null ? null : (
                <PerformancePagination
                  page={pageInfo}
                  label={t("paginationLabel")}
                  onPageChange={(page) => change({ page })}
                />
              )}
            </>
          )}
        </Card>
      </section>

      {formOpen ? (
        <ReviewFormDialog
          key={editing?.id ?? "new"}
          open
          onOpenChange={setFormOpen}
          review={editing}
        />
      ) : null}
      {stateAction === null ? null : (
        <ReviewStateDialog
          key={`${stateAction.review.id}-${stateAction.action}`}
          open
          onOpenChange={(open) => {
            if (!open) setStateAction(null);
          }}
          review={stateAction.review}
          employeeName={employeeName(stateAction.review)}
          action={stateAction.action}
        />
      )}
      {detail === null ? null : (
        <ReviewDetailDialog
          key={detail.id}
          open
          onOpenChange={(open) => {
            if (!open) setDetail(null);
          }}
          review={detail}
          employeeName={employeeName(detail)}
          showFeedback={canViewFeedback && !isSubjectOf(detail)}
        />
      )}
      {feedbackFor === null ? null : (
        <FeedbackDialog
          key={feedbackFor.id}
          open
          onOpenChange={(open) => {
            if (!open) setFeedbackFor(null);
          }}
          review={feedbackFor}
          employeeName={employeeName(feedbackFor)}
        />
      )}
    </div>
  );
}
