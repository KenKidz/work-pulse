"use client";

/**
 * Read-only review detail: the completed review the subject sees (rating,
 * period, strengths, improvements) and the author/HR view of the same record.
 * Feedback is appended only when the viewer has standing and is not the
 * subject — the subject's surface never mounts the feedback panel. Client leaf.
 */

import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { formatPerformanceTimestamp } from "../dates";
import { reviewStatusVariant } from "../labels";
import type { Review } from "../types";
import { FeedbackPanel } from "./FeedbackPanel";

type ReviewDetailDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  review: Review;
  employeeName: string;
  /** Author/HR standing on a review the caller is not the subject of. */
  showFeedback: boolean;
};

export function ReviewDetailDialog({
  open,
  onOpenChange,
  review,
  employeeName,
  showFeedback,
}: ReviewDetailDialogProps) {
  const t = useTranslations("performance.reviews.detail");
  const status = useTranslations("performance.status.review");
  const locale = useLocale();
  const isDraft = review.status === "draft";
  const rating = review.rating;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>
            {review.period === null || review.period === undefined
              ? t("descriptionNoPeriod", { name: employeeName })
              : t("description", { name: employeeName, period: review.period })}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={reviewStatusVariant[review.status]} data-status={review.status}>
            {status(review.status)}
          </Badge>
          {isDraft ? <p className="text-xs text-muted-foreground">{t("draftNotice")}</p> : null}
        </div>

        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">{t("rating")}</dt>
            <dd className="font-mono tabular-nums">
              {rating === null || rating === undefined ? (
                <span className="text-muted-foreground">{t("noRating")}</span>
              ) : (
                t("ratingValue", { rating })
              )}
            </dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs text-muted-foreground">{t("completed")}</dt>
            <dd>
              {review.completed_at === null || review.completed_at === undefined ? (
                <span className="text-muted-foreground">{t("notCompleted")}</span>
              ) : (
                <time dateTime={review.completed_at}>
                  {formatPerformanceTimestamp(review.completed_at, locale)}
                </time>
              )}
            </dd>
          </div>
        </dl>

        <section className="space-y-1">
          <h3 className="text-sm font-medium">{t("strengths")}</h3>
          <p className="text-sm text-pretty whitespace-pre-line">
            {review.strengths === null || review.strengths === undefined ? (
              <span className="text-muted-foreground">{t("notRecorded")}</span>
            ) : (
              review.strengths
            )}
          </p>
        </section>

        <section className="space-y-1">
          <h3 className="text-sm font-medium">{t("improvements")}</h3>
          <p className="text-sm text-pretty whitespace-pre-line">
            {review.improvements === null || review.improvements === undefined ? (
              <span className="text-muted-foreground">{t("notRecorded")}</span>
            ) : (
              review.improvements
            )}
          </p>
        </section>

        {showFeedback ? <FeedbackPanel reviewId={review.id} /> : null}
      </DialogContent>
    </Dialog>
  );
}
