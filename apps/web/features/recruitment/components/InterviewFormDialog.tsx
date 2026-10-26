"use client";

/**
 * Schedule / reschedule dialog for an interview. Scheduling picks an
 * `interviewing` candidate and an active employee; editing keeps the candidate
 * fixed and only sends the fields that changed (the API records the audit diff
 * from the provided fields). The server stays the authority: pinned codes map
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

import { localDateTimeToUtc, toLocalDateTimeInput } from "../dates";
import {
  interviewFieldErrors,
  useActiveEmployees,
  useInterviewSchedule,
  useInterviewUpdate,
} from "../hooks/use-recruitment";
import { interviewScheduleSchema, type InterviewScheduleValues } from "../schema";
import type { Candidate, Interview } from "../types";

type InterviewFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = schedule; an interview = reschedule / re-assign. */
  interview: Interview | null;
  /** The `interviewing` candidates eligible for scheduling. */
  candidates: Candidate[];
  candidatesPending?: boolean;
  /** The fixed candidate label in edit mode. */
  candidateName?: string;
};

export function InterviewFormDialog({
  open,
  onOpenChange,
  interview,
  candidates,
  candidatesPending = false,
  candidateName,
}: InterviewFormDialogProps) {
  const t = useTranslations("recruitment.interviewForm");
  const common = useTranslations("recruitment.actions");
  const validation = useTranslations("recruitment.validation");
  const errors = useTranslations("recruitment.errors");
  const isEdit = interview !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const scheduleInterview = useInterviewSchedule();
  const updateInterview = useInterviewUpdate();
  const mutation = isEdit ? updateInterview : scheduleInterview;
  const interviewers = useActiveEmployees();

  const defaultCandidateId = candidates.length === 1 ? candidates[0]?.id : "";

  const form = useForm<InterviewScheduleValues>({
    resolver: zodResolver(interviewScheduleSchema((key) => validation(key))),
    defaultValues: {
      candidate_id: interview?.candidate_id ?? defaultCandidateId ?? "",
      interviewer_id: interview?.interviewer_id ?? "",
      scheduled_at: interview === null ? "" : toLocalDateTimeInput(interview.scheduled_at),
      notes: interview?.notes ?? "",
    },
  });

  // The candidate directory can finish loading after the dialog opened; once
  // exactly one interviewing candidate is known, preselect it.
  useEffect(() => {
    if (isEdit || candidates.length !== 1) return;
    const candidateId = candidates[0]?.id;
    if (candidateId !== undefined && form.getValues("candidate_id") === "") {
      form.setValue("candidate_id", candidateId);
    }
  }, [candidates, form, isEdit]);

  async function onSubmit(values: InterviewScheduleValues) {
    setRootError(null);
    const notes = values.notes === "" ? null : values.notes;

    try {
      if (interview === null) {
        await scheduleInterview.mutateAsync({
          candidateId: values.candidate_id,
          data: {
            interviewer_id: values.interviewer_id,
            scheduled_at: localDateTimeToUtc(values.scheduled_at),
            notes,
          },
        });
      } else {
        // Only the changed fields: an empty scheduled date means the same
        // instant, and the server records an audit diff per sent field.
        const data: {
          interviewer_id?: string;
          scheduled_at?: string;
          notes?: string | null;
        } = {};
        if (values.interviewer_id !== interview.interviewer_id) {
          data.interviewer_id = values.interviewer_id;
        }
        const localScheduledAt = toLocalDateTimeInput(interview.scheduled_at);
        if (values.scheduled_at !== localScheduledAt) {
          data.scheduled_at = localDateTimeToUtc(values.scheduled_at);
        }
        if ((interview.notes ?? "") !== values.notes) {
          data.notes = notes;
        }
        if (Object.keys(data).length === 0) {
          onOpenChange(false);
          return;
        }
        await updateInterview.mutateAsync({ interviewId: interview.id, data });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = interviewFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root" || field === "candidate_id") {
          setRootError(message ?? null);
        } else {
          form.setError(field as "interviewer_id" | "scheduled_at" | "notes", {
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
                <p className="text-sm font-medium">Candidate</p>
                <p className="text-sm text-muted-foreground">{candidateName ?? "This candidate"}</p>
              </div>
            ) : (
              <FormField
                control={form.control}
                name="candidate_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("candidateHint")}>{t("candidate")}</FormLabel>
                    {candidates.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {candidatesPending
                          ? t("loadingCandidates")
                          : t("noCandidates")}
                      </p>
                    ) : (
                      <>
                        <Select value={field.value} onValueChange={field.onChange}>
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder={t("selectCandidate")} />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {candidates.map((candidate) => (
                              <SelectItem key={candidate.id} value={candidate.id}>
                                {candidate.first_name} {candidate.last_name}
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
                name="interviewer_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("interviewerHint")}>{t("interviewer")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("selectEmployee")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {interviewers.map((employee) => (
                          <SelectItem key={employee.id} value={employee.id}>
                            {employee.first_name} {employee.last_name}
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
                name="scheduled_at"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("scheduledAtHint")}>{t("scheduledAt")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} type="datetime-local" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

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
                disabled={mutation.isPending || (!isEdit && candidates.length === 0)}
              >
                {mutation.isPending
                  ? isEdit
                    ? t("saving")
                    : t("scheduling")
                  : isEdit
                    ? t("save")
                    : t("schedule")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
