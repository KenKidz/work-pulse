"use client";

/**
 * Remove one employee-skill row. Unlike archiving a catalog skill, removing a
 * row is final (audited, never evented); the copy says so before the call.
 * Client leaf.
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

import { performanceErrorMessage } from "../labels";
import { useEmployeeSkillRemove } from "../hooks/use-performance";
import type { EmployeeSkill } from "../types";

type EmployeeSkillRemoveDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employeeId: string;
  employeeName: string;
  employeeSkill: EmployeeSkill;
};

export function EmployeeSkillRemoveDialog({
  open,
  onOpenChange,
  employeeId,
  employeeName,
  employeeSkill,
}: EmployeeSkillRemoveDialogProps) {
  const t = useTranslations("performance.skills.employeeRemove");
  const common = useTranslations("performance.actions");
  const errors = useTranslations("performance.errors");
  const removeRow = useEmployeeSkillRemove(employeeId);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setError(null);
    try {
      await removeRow.mutateAsync({ employeeId, skillId: employeeSkill.skill_id });
      onOpenChange(false);
    } catch (failure) {
      setError(performanceErrorMessage(failure, errors));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>
            {t("description", { skill: employeeSkill.skill_name, name: employeeName })}
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
            variant="destructive"
            disabled={removeRow.isPending}
            onClick={() => void confirm()}
          >
            {removeRow.isPending ? t("removing") : t("removeAction")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
