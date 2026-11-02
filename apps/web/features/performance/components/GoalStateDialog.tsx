"use client";

/**
 * Goal lifecycle confirmation. Every transition is a deliberate action:
 * activating starts progress tracking, completing and cancelling are terminal.
 * The server's pinned `409` (`goal_not_draft` / `goal_not_active`) renders in
 * place, so a stale row keeps the dialog open with the mapped sentence. Client
 * leaf.
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
import { useGoalActivate, useGoalCancel, useGoalComplete } from "../hooks/use-performance";
import type { Goal } from "../types";

export type GoalStateAction = "activate" | "complete" | "cancel";

type GoalStateDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  goal: Goal;
  action: GoalStateAction;
};

export function GoalStateDialog({ open, onOpenChange, goal, action }: GoalStateDialogProps) {
  const t = useTranslations("performance.goalState");
  const common = useTranslations("performance.actions");
  const errors = useApiErrorTranslator("performance.errors");
  const activate = useGoalActivate();
  const complete = useGoalComplete();
  const cancel = useGoalCancel();
  const mutation = action === "activate" ? activate : action === "complete" ? complete : cancel;
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setError(null);
    try {
      await mutation.mutateAsync({ id: goal.id });
      onOpenChange(false);
    } catch (failure) {
      setError(performanceErrorMessage(failure, errors));
    }
  }

  const title =
    action === "activate"
      ? t("activateTitle")
      : action === "complete"
        ? t("completeTitle")
        : t("cancelTitle");
  const description =
    action === "activate"
      ? t("activateDescription", { title: goal.title })
      : action === "complete"
        ? t("completeDescription", { title: goal.title })
        : t("cancelDescription", { title: goal.title });
  const pendingLabel =
    action === "activate"
      ? t("activating")
      : action === "complete"
        ? t("completing")
        : t("cancelling");
  const actionLabel =
    action === "activate"
      ? common("activate")
      : action === "complete"
        ? common("complete")
        : t("cancelAction");

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
          <Button type="button" disabled={mutation.isPending} onClick={() => void confirm()}>
            {mutation.isPending ? pendingLabel : actionLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
