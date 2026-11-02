"use client";

/**
 * Create/edit dialog for a candidate — HR-entered intake on an open job, or
 * a contact-field edit. RHF + zod mirror the pinned `CandidateCreate` /
 * `CandidateUpdate` bounds; the server stays the authority and its codes map
 * back to the field that caused them. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
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
  candidateFieldErrors,
  useCandidateCreate,
  useCandidateUpdate,
  type CandidateFieldName,
} from "../hooks/use-recruitment";
import { candidateCreateSchema, type CandidateCreateValues } from "../schema";
import type { Candidate, Job } from "../types";

type CandidateFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = intake (create) mode; a candidate = contact edit. */
  candidate: Candidate | null;
  /** Jobs eligible for intake (`open`) — create mode only. */
  openJobs: Job[];
  /** The job currently filtered in the pipeline, preselected when open. */
  initialJobId?: string;
  /** Human job title for the read-only line in edit mode. */
  jobTitle?: string;
  /** The job directory is still loading (create mode). */
  jobsPending?: boolean;
};

export function CandidateFormDialog({
  open,
  onOpenChange,
  candidate,
  openJobs,
  initialJobId,
  jobTitle,
  jobsPending = false,
}: CandidateFormDialogProps) {
  const t = useTranslations("recruitment.candidateForm");
  const common = useTranslations("recruitment.actions");
  const validation = useTranslations("recruitment.validation");
  const errors = useApiErrorTranslator("recruitment.errors");
  const sources = useTranslations("recruitment.sources");
  const isEdit = candidate !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createCandidate = useCandidateCreate();
  const updateCandidate = useCandidateUpdate();
  const mutation = isEdit ? updateCandidate : createCandidate;

  const defaultJobId =
    initialJobId !== undefined && openJobs.some((job) => job.id === initialJobId)
      ? initialJobId
      : openJobs.length === 1
        ? openJobs[0]?.id
        : "";

  // One schema for both modes: edit keeps the candidate's existing job id in
  // the hidden form state (it is a valid uuid and never submitted), so the
  // shared intake shape validates without a resolver cast.
  const form = useForm<CandidateCreateValues>({
    resolver: zodResolver(candidateCreateSchema((key) => validation(key))),
    defaultValues: {
      job_id: candidate?.job_id ?? defaultJobId ?? "",
      first_name: candidate?.first_name ?? "",
      last_name: candidate?.last_name ?? "",
      email: candidate?.email ?? "",
      phone: candidate?.phone ?? "",
      source: candidate?.source ?? "referral",
      notes: candidate?.notes ?? "",
    },
  });

  // The directory can finish loading after the dialog opened; once exactly one
  // open job is known, preselect it instead of leaving intake blocked on a
  // select the user has no real choice about.
  useEffect(() => {
    if (isEdit || openJobs.length !== 1) return;
    const jobId = openJobs[0]?.id;
    if (jobId !== undefined && form.getValues("job_id") === "") {
      form.setValue("job_id", jobId);
    }
  }, [form, isEdit, openJobs]);

  async function onSubmit(values: CandidateCreateValues) {
    setRootError(null);
    const contact = {
      first_name: values.first_name,
      last_name: values.last_name,
      email: values.email,
      phone: values.phone === "" ? null : values.phone,
      source: values.source,
      notes: values.notes === "" ? null : values.notes,
    };

    try {
      if (candidate === null) {
        await createCandidate.mutateAsync({ jobId: values.job_id, data: contact });
      } else {
        await updateCandidate.mutateAsync({ candidateId: candidate.id, data: contact });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = candidateFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root" || (field === "job_id" && isEdit)) {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<CandidateFieldName, "root">, {
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
            {isEdit ? (
              <div className="space-y-2">
                <p className="text-sm font-medium">Job</p>
                <p className="text-sm text-muted-foreground">{jobTitle ?? "This job"}</p>
              </div>
            ) : (
              <FormField
                control={form.control}
                name="job_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("jobHint")}>{t("job")}</FormLabel>
                    {openJobs.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {jobsPending ? t("loadingOpenJobs") : t("noOpenJobs")}
                      </p>
                    ) : (
                      <>
                        <Select value={field.value} onValueChange={field.onChange}>
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder={t("selectOpenJob")} />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {openJobs.map((job) => (
                              <SelectItem key={job.id} value={job.id}>
                                {job.title}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </>
                    )}
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="first_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("firstName")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="last_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("lastName")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("email")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} type="email" autoComplete="off" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="phone"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("phone")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} type="tel" autoComplete="off" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="source"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("source")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder={t("sourcePlaceholder")} />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="referral">{sources("referral")}</SelectItem>
                      <SelectItem value="job_board">{sources("job_board")}</SelectItem>
                      <SelectItem value="agency">{sources("agency")}</SelectItem>
                      <SelectItem value="other">{sources("other")}</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="notes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("notesHint")}>{t("notes")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value}
                      rows={4}
                      placeholder={t("notesPlaceholder")}
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
              <Button
                type="submit"
                disabled={mutation.isPending || (!isEdit && (openJobs.length === 0 || jobsPending))}
              >
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
