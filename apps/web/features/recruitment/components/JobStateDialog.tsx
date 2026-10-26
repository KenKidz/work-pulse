"use client";

/**
 * Open/close confirmation for a job. Opening is additive; closing is terminal
 * and stops hiring, so both transitions are confirmed and the server's pinned
 * `409` (`job_not_draft` / `job_not_open`) renders in place. Client leaf.
 */

import { CircleAlert } from "lucide-react";
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
      setError(recruitmentErrorMessage(failure));
    }
  }

  const isOpening = action === "open";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isOpening ? "Open job" : "Close job"}</DialogTitle>
          <DialogDescription>
            {isOpening
              ? `Open “${job.title}” so HR can add candidates and hire against its openings.`
              : `Close “${job.title}”? Closing is terminal — the job cannot be re-opened, and new candidates and hires will be refused. Candidates already in the pipeline can still be moved.`}
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
            Cancel
          </Button>
          <Button
            type="button"
            variant={isOpening ? "default" : "destructive"}
            disabled={mutation.isPending}
            onClick={() => void confirm()}
          >
            {mutation.isPending
              ? isOpening
                ? "Opening…"
                : "Closing…"
              : isOpening
                ? "Open job"
                : "Close job"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
