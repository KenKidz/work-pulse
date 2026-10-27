"use client";

/**
 * Attach/update one employee-skill row. Create picks from the **active**
 * catalog (archived rows never appear, pinned `409 skill_archived` is the
 * server backstop); edit keeps the skill fixed and replaces proficiency and
 * note in place. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import {
  employeeSkillFieldErrors,
  useEmployeeSkillCreate,
  useEmployeeSkillUpdate,
  type EmployeeSkillFieldName,
} from "../hooks/use-performance";
import { employeeSkillFormSchema, PROFICIENCIES, type EmployeeSkillFormValues } from "../schema";
import type { EmployeeSkill, Skill } from "../types";

type EmployeeSkillFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employeeId: string;
  employeeName: string;
  /** Active catalog rows only — archived skills cannot be newly attached. */
  skills: Skill[];
  /** `null` = attach; a row = update proficiency/note. */
  employeeSkill: EmployeeSkill | null;
};

export function EmployeeSkillFormDialog({
  open,
  onOpenChange,
  employeeId,
  employeeName,
  skills,
  employeeSkill,
}: EmployeeSkillFormDialogProps) {
  const t = useTranslations("performance.skills.employeeForm");
  const common = useTranslations("performance.actions");
  const validation = useTranslations("performance.validation");
  const errors = useTranslations("performance.errors");
  const levels = useTranslations("performance.skills.levels");
  const isEdit = employeeSkill !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createRow = useEmployeeSkillCreate(employeeId);
  const updateRow = useEmployeeSkillUpdate(employeeId);
  const mutation = isEdit ? updateRow : createRow;

  const form = useForm<EmployeeSkillFormValues>({
    resolver: zodResolver(employeeSkillFormSchema((key) => validation(key))),
    defaultValues: {
      skill_id: employeeSkill?.skill_id ?? "",
      proficiency:
        employeeSkill === null || employeeSkill.proficiency === null
          ? ""
          : String(employeeSkill.proficiency),
      note: employeeSkill?.note ?? "",
    },
  });

  async function onSubmit(values: EmployeeSkillFormValues) {
    setRootError(null);
    const note = values.note.trim() === "" ? null : values.note.trim();

    try {
      if (employeeSkill === null) {
        await createRow.mutateAsync({
          employeeId,
          data: {
            skill_id: values.skill_id,
            proficiency: Number(values.proficiency),
            note,
          },
        });
      } else {
        await updateRow.mutateAsync({
          employeeId,
          skillId: employeeSkill.skill_id,
          data: {
            proficiency: Number(values.proficiency),
            note,
          },
        });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = employeeSkillFieldErrors(error, errors, {
        hasSkillField: !isEdit,
      });
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<EmployeeSkillFieldName, "root">, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("editTitle") : t("createTitle")}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? t("editDescription", { name: employeeName })
              : t("createDescription", { name: employeeName })}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {isEdit ? (
              <div className="space-y-1">
                <p className="text-sm font-medium">{t("skill")}</p>
                <p className="text-sm text-muted-foreground">{employeeSkill.skill_name}</p>
              </div>
            ) : (
              <FormField
                control={form.control}
                name="skill_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("skillHint")}>{t("skill")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("selectSkill")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {skills.map((skill) => (
                          <SelectItem key={skill.id} value={skill.id}>
                            {skill.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <FormField
              control={form.control}
              name="proficiency"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("proficiencyHint")}>{t("proficiency")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder={t("selectProficiency")} />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {PROFICIENCIES.map((value) => (
                        <SelectItem key={value} value={value}>
                          {levels("option", { level: value })}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="note"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("noteHint")}>{t("note")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value}
                      rows={3}
                      placeholder={t("notePlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {rootError ? (
              <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {rootError}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending
                  ? isEdit
                    ? t("saving")
                    : t("adding")
                  : isEdit
                    ? t("save")
                    : t("add")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
