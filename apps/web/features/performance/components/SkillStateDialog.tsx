"use client";

/**
 * Catalog archive/restore confirmation (ADR 0007 soft delete). Archiving never
 * blocks history: employees keep the skill and can still edit their level; only
 * new assignment stops. Both actions are no-ops on an already-target-state row
 * (the API returns 200 without an audit entry). Client leaf.
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
import { useSkillArchive, useSkillRestore } from "../hooks/use-performance";
import type { Skill } from "../types";

export type SkillStateAction = "archive" | "restore";

type SkillStateDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  skill: Skill;
  action: SkillStateAction;
};

export function SkillStateDialog({ open, onOpenChange, skill, action }: SkillStateDialogProps) {
  const t = useTranslations("performance.skills.state");
  const common = useTranslations("performance.actions");
  const errors = useApiErrorTranslator("performance.errors");
  const archive = useSkillArchive();
  const restore = useSkillRestore();
  const mutation = action === "archive" ? archive : restore;
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setError(null);
    try {
      await mutation.mutateAsync({ id: skill.id });
      onOpenChange(false);
    } catch (failure) {
      setError(performanceErrorMessage(failure, errors));
    }
  }

  const isArchive = action === "archive";
  const title = isArchive ? t("archiveTitle") : t("restoreTitle");
  const description = isArchive
    ? t("archiveDescription", { name: skill.name })
    : t("restoreDescription", { name: skill.name });
  const pendingLabel = isArchive ? t("archiving") : t("restoring");
  const actionLabel = isArchive ? t("archiveAction") : t("restoreAction");

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
