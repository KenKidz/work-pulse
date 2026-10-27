"use client";

/**
 * Feedback the reviewer may read: every comment for the author and HR, the
 * caller's own for a nominated peer. Mounted only for a viewer with feedback
 * standing — the subject never gets this surface. A `403` is explained inline
 * (a manager who can see the review but is neither its author nor a provider),
 * never leaked as data. Client leaf.
 */

import { CircleAlert, MessageSquare } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { useListReviewFeedback } from "@workpulse/api-client";

import { formatPerformanceTimestamp } from "../dates";
import { employeeLabel, isForbiddenError } from "../labels";
import { usePerformanceDirectory } from "../hooks/use-performance";

export function FeedbackPanel({ reviewId }: { reviewId: string }) {
  const t = useTranslations("performance.feedback.panel");
  const common = useTranslations("performance.actions");
  const fallbacks = useTranslations("performance.fallbacks");
  const locale = useLocale();
  const directory = usePerformanceDirectory();
  const feedbackQuery = useListReviewFeedback(reviewId);
  const items = feedbackQuery.data?.status === 200 ? feedbackQuery.data.data.items : [];

  return (
    <section aria-labelledby={`review-feedback-title-${reviewId}`} className="space-y-3">
      <div className="space-y-1">
        <h3 id={`review-feedback-title-${reviewId}`} className="text-sm font-medium">
          {t("title")}
        </h3>
        <p className="text-sm text-pretty text-muted-foreground">{t("description")}</p>
      </div>

      {feedbackQuery.isPending ? (
        <div className="flex items-center gap-2 rounded-md border px-4 py-6 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>{t("loading")}</p>
        </div>
      ) : feedbackQuery.isError ? (
        isForbiddenError(feedbackQuery.error) ? (
          <p className="rounded-md border border-dashed px-4 py-6 text-sm text-muted-foreground">
            {t("denied")}
          </p>
        ) : (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>{t("loadError")}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void feedbackQuery.refetch()}
              >
                {common("retry")}
              </Button>
            </AlertDescription>
          </Alert>
        )
      ) : items.length === 0 ? (
        <EmptyState icon={MessageSquare} message={t("empty")} />
      ) : (
        <ul className="divide-y rounded-md border">
          {items.map((item) => (
            <li key={item.id} className="space-y-1 px-4 py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-sm font-medium">
                  {employeeLabel(
                    directory.nameById,
                    item.author_id,
                    fallbacks("employee", { id: item.author_id.slice(-4) }),
                  )}
                </p>
                <time dateTime={item.created_at} className="text-xs text-muted-foreground">
                  {formatPerformanceTimestamp(item.created_at, locale)}
                </time>
              </div>
              <p className="text-sm text-pretty whitespace-pre-line">{item.comment}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
