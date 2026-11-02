"use client";

/**
 * Create/edit dialog for a hiring job — RHF + zod against the pinned
 * `JobCreate`/`JobUpdate` shapes. A job is always created as `draft`; opening
 * is a separate action. Archived organization references stay visible (and
 * disabled) when a job already uses one, so editing cannot silently drop a
 * reference. Server errors map back to the field that caused them. Client
 * leaf.
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { useApiErrorTranslator } from "@/lib/api-errors";

import {
  jobFieldErrors,
  useJobCreate,
  useJobUpdate,
  useRecruitmentDirectory,
  type JobFieldName,
} from "../hooks/use-recruitment";
import { jobFormSchema, type JobFormValues } from "../schema";
import type { Job } from "../types";

type JobFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a job = edit (draft/open only). */
  job: Job | null;
};

/** Archived references stay readable but cannot be newly picked. */
function isArchived(option: { archived_at?: string | null }): boolean {
  return option.archived_at !== null && option.archived_at !== undefined;
}

export function JobFormDialog({ open, onOpenChange, job }: JobFormDialogProps) {
  const t = useTranslations("recruitment.jobForm");
  const common = useTranslations("recruitment.actions");
  const validation = useTranslations("recruitment.validation");
  const errors = useApiErrorTranslator("recruitment.errors");
  const isEdit = job !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createJob = useJobCreate();
  const updateJob = useJobUpdate();
  const mutation = isEdit ? updateJob : createJob;
  const directory = useRecruitmentDirectory();

  const form = useForm<JobFormValues>({
    resolver: zodResolver(jobFormSchema((key) => validation(key))),
    defaultValues: {
      title: job?.title ?? "",
      description: job?.description ?? "",
      department_id: job?.department_id ?? "",
      position_id: job?.position_id ?? "",
      location_id: job?.location_id ?? "",
      openings: job?.openings ?? 1,
    },
  });

  async function onSubmit(values: JobFormValues) {
    setRootError(null);
    const data = {
      title: values.title.trim(),
      description: values.description.trim() === "" ? null : values.description.trim(),
      department_id: values.department_id,
      position_id: values.position_id,
      location_id: values.location_id,
      openings: values.openings,
    };

    try {
      if (job === null) {
        await createJob.mutateAsync({ data });
      } else {
        await updateJob.mutateAsync({ jobId: job.id, data });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = jobFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<JobFieldName, "root">, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("editTitle") : t("createTitle")}</DialogTitle>
          <DialogDescription>
            {isEdit ? t("editDescription") : t("createDescription")}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="title"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("title")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} placeholder={t("titlePlaceholder")} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="openings"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("openingsHint")}>{t("openings")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        type="number"
                        min={1}
                        step={1}
                        inputMode="numeric"
                        onChange={(event) =>
                          field.onChange(event.target.value === "" ? undefined : event.target.value)
                        }
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

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

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="department_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("department")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("selectDepartment")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {directory.departments.map((option) => (
                          <SelectItem
                            key={option.id}
                            value={option.id}
                            disabled={isArchived(option) && option.id !== field.value}
                          >
                            {option.name}
                            {isArchived(option) ? " (archived)" : ""}
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
                name="position_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("position")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("selectPosition")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {directory.positions.map((option) => (
                          <SelectItem
                            key={option.id}
                            value={option.id}
                            disabled={isArchived(option) && option.id !== field.value}
                          >
                            {option.title}
                            {isArchived(option) ? " (archived)" : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="location_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("location")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder={t("selectLocation")} />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {directory.locations.map((option) => (
                        <SelectItem
                          key={option.id}
                          value={option.id}
                          disabled={isArchived(option) && option.id !== field.value}
                        >
                          {option.name}
                          {isArchived(option) ? " (archived)" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
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
