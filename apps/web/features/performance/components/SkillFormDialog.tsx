"use client";

/**
 * Create/rename dialog for the tenant skill catalog — RHF + zod against the
 * pinned `SkillCreate`/`SkillUpdate` shapes. A duplicate name (archived rows
 * included) comes back as `409 skill_name_taken` on the name field while the
 * typed name stays in place. Client leaf.
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
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import { skillFieldErrors, useSkillCreate, useSkillUpdate } from "../hooks/use-performance";
import { skillFormSchema, type SkillFormValues } from "../schema";
import type { Skill } from "../types";

type SkillFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a skill = rename/re-describe. */
  skill: Skill | null;
};

export function SkillFormDialog({ open, onOpenChange, skill }: SkillFormDialogProps) {
  const t = useTranslations("performance.skills.form");
  const common = useTranslations("performance.actions");
  const validation = useTranslations("performance.validation");
  const errors = useTranslations("performance.errors");
  const isEdit = skill !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createSkill = useSkillCreate();
  const updateSkill = useSkillUpdate();
  const mutation = isEdit ? updateSkill : createSkill;

  const form = useForm<SkillFormValues>({
    resolver: zodResolver(skillFormSchema((key) => validation(key))),
    defaultValues: {
      name: skill?.name ?? "",
      description: skill?.description ?? "",
    },
  });

  async function onSubmit(values: SkillFormValues) {
    setRootError(null);
    const data = {
      name: values.name.trim(),
      description: values.description.trim() === "" ? null : values.description.trim(),
    };

    try {
      if (skill === null) {
        await createSkill.mutateAsync({ data });
      } else {
        await updateSkill.mutateAsync({ id: skill.id, data });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = skillFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as "name", { type: "server", message });
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
            {isEdit ? t("editDescription") : t("createDescription")}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("nameHint")}>{t("name")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      value={field.value}
                      placeholder={t("namePlaceholder")}
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("descriptionHint")}>{t("description")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value}
                      rows={4}
                      placeholder={t("descriptionPlaceholder")}
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
                    : t("creating")
                  : isEdit
                    ? t("save")
                    : t("create")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
