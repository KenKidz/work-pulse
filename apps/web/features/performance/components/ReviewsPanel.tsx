"use client";

/**
 * Reviews tab — the caller's visible reviews (the API scopes the list: a
 * subject sees only completed rows, a manager sees direct reports, management
 * sees the tenant). T11 adds the author workspace, nomination and feedback;
 * this read surface already carries the subject's "my reviews" view. Client
 * leaf.
 */

import { CircleAlert, ClipboardCheck } from "lucide-react";
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

import { formatPerformanceTimestamp } from "../dates";
import { employeeLabel, isForbiddenError, reviewStatusVariant } from "../labels";
import {
  usePerformanceAccess,
  usePerformanceDirectory,
  useReviews,
} from "../hooks/use-performance";
import { PerformanceDeniedState } from "./PerformanceDeniedState";
import { PerformancePagination } from "./PerformancePagination";

const REVIEWS_PAGE_SIZE = 20;

export function ReviewsPanel() {
  const t = useTranslations("performance.reviews");
  const common = useTranslations("performance.actions");
  const status = useTranslations("performance.status.review");
  const fallbacks = useTranslations("performance.fallbacks");
  const locale = useLocale();
  const { canViewTeam } = usePerformanceAccess();
  const directory = usePerformanceDirectory();
  const [page, setPage] = useState(1);

  const reviewsQuery = useReviews({ page, page_size: REVIEWS_PAGE_SIZE });
  const reviews = reviewsQuery.data?.status === 200 ? reviewsQuery.data.data.items : [];
  const pageInfo = reviewsQuery.data?.status === 200 ? reviewsQuery.data.data.page : null;
  const pageOutOfRange = pageInfo !== null && pageInfo.page > Math.max(pageInfo.total_pages, 1);

  return (
    <section aria-labelledby="performance-reviews-title" className="space-y-4">
      <div className="space-y-1">
        <h2 id="performance-reviews-title" className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p className="max-w-2xl text-sm text-pretty text-muted-foreground">
          {canViewTeam ? t("description") : t("myDescription")}
        </p>
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
            <EmptyState icon={ClipboardCheck} message={canViewTeam ? t("empty") : t("emptyMine")} />
            {pageOutOfRange ? (
              <div className="mt-3 flex justify-center">
                <Button variant="outline" size="sm" onClick={() => setPage(1)}>
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
                  <TableHead className="pr-3">{t("columns.completed")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {reviews.map((review) => {
                  const rating = review.rating;
                  return (
                    <TableRow key={review.id}>
                      {canViewTeam ? (
                        <TableCell className="pl-3 font-medium">
                          {employeeLabel(
                            directory.nameById,
                            review.employee_id,
                            fallbacks("employee", {
                              id: review.employee_id.slice(-4),
                            }),
                          )}
                        </TableCell>
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
                      <TableCell className="pr-3 text-sm text-muted-foreground">
                        {review.completed_at === null || review.completed_at === undefined ? (
                          "—"
                        ) : (
                          <time dateTime={review.completed_at}>
                            {formatPerformanceTimestamp(review.completed_at, locale)}
                          </time>
                        )}
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
                onPageChange={setPage}
              />
            )}
          </>
        )}
      </Card>
    </section>
  );
}
