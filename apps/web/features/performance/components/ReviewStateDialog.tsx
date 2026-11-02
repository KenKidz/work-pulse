"use client";

/**
 * Review lifecycle confirmation. Completing publishes the review to the
 * subject and is terminal; cancelling keeps draft feedback readable to the
 * author/HR but accepts no more submissions. The server's pinned `409`
 * (`review_incomplete` / `review_not_draft`) renders in place, so a stale row
 * keeps the dialog open with the mapped sentence. Client leaf.
 */

import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { useApiErrorTranslator } from "@/lib/api-errors";

import { performanceErrorMessage } from "../labels";
import { useReviewCancel, useReviewComplete } from "../hooks/use-performance";
import type { Review } from "../types";

export type ReviewStateAction = "complete" | "cancel";

type ReviewStateDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  review: Review;
  employeeName: string;
  action: ReviewStateAction;
};

export function ReviewStateDialog({
  open,
  onOpenChange,
  review,
  employeeName,
  action,
}: ReviewStateDialogProps) {
  const t = useTranslations("performance.reviews.state");
  const common = useTranslations("performance.actions");
  const errors = useApiErrorTranslator("performance.errors");
  const complete = useReviewComplete();
  const cancel = useReviewCancel();
  const mutation = action === "complete" ? complete : cancel;
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setError(null);
    try {
      await mutation.mutateAsync({ id: review.id });
      onOpenChange(false);
    } catch (failure) {
      setError(performanceErrorMessage(failure, errors));
    }
  }

  const isComplete = action === "complete";
  const title = isComplete ? t("completeTitle") : t("cancelTitle");
  const description = isComplete
    ? t("completeDescription", { name: employeeName })
    : t("cancelDescription", { name: employeeName });
  const pendingLabel = isComplete ? t("completing") : t("cancelling");
  const actionLabel = isComplete ? t("completeAction") : t("cancelAction");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {error ? (
          <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {common("cancel")}
          </Button>
          <Button
            type="button"
            variant={isComplete ? "default" : "destructive"}
            disabled={mutation.isPending}
            onClick={() => void confirm()}
          >
            {mutation.isPending ? pendingLabel : actionLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
