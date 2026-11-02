"use client";

/**
 * Open/close confirmation for a job. Opening is additive; closing is terminal
 * and stops hiring, so both transitions are confirmed and the server's pinned
 * `409` (`job_not_draft` / `job_not_open`) renders in place. Client leaf.
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

import { recruitmentErrorMessage } from "../labels";
import { useJobClose, useJobOpen } from "../hooks/use-recruitment";
import type { Job } from "../types";

export type JobStateAction = "open" | "close";

type JobStateDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  job: Job;
  action: JobStateAction;
};

export function JobStateDialog({ open, onOpenChange, job, action }: JobStateDialogProps) {
  const t = useTranslations("recruitment.jobState");
  const common = useTranslations("recruitment.actions");
  const errors = useApiErrorTranslator("recruitment.errors");
  const openJob = useJobOpen();
  const closeJob = useJobClose();
  const mutation = action === "open" ? openJob : closeJob;
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setError(null);
    try {
      await mutation.mutateAsync({ jobId: job.id });
      onOpenChange(false);
    } catch (failure) {
      setError(recruitmentErrorMessage(failure, errors));
    }
  }

  const isOpening = action === "open";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isOpening ? t("openTitle") : t("closeTitle")}</DialogTitle>
          <DialogDescription>
            {isOpening
              ? t("openDescription", { title: job.title })
              : t("closeDescription", { title: job.title })}
          </DialogDescription>
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
            variant={isOpening ? "default" : "destructive"}
            disabled={mutation.isPending}
            onClick={() => void confirm()}
          >
            {mutation.isPending
              ? isOpening
                ? t("opening")
                : t("closing")
              : isOpening
                ? t("openTitle")
                : t("closeTitle")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
