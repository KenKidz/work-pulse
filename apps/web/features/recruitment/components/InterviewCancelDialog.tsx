"use client";

/**
 * Cancel confirmation for a scheduled interview. Cancellation is terminal, so
 * it is confirmed; the optional note is PII (stored, never emitted). The
 * server's pinned `409 interview_not_scheduled` renders in place when the row
 * moved underneath the dialog. Client leaf.
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
import { Textarea } from "@/components/ui/textarea";

import { useApiErrorTranslator } from "@/lib/api-errors";

import { formatRecruitmentTimestamp } from "../dates";
import { recruitmentErrorMessage } from "../labels";
import { useInterviewCancel } from "../hooks/use-recruitment";
import { candidateDecisionSchema, type CandidateDecisionValues } from "../schema";
import type { Interview } from "../types";

type InterviewCancelDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  interview: Interview;
  candidateName?: string;
};

export function InterviewCancelDialog({
  open,
  onOpenChange,
  interview,
  candidateName,
}: InterviewCancelDialogProps) {
  const t = useTranslations("recruitment.cancel");
  const common = useTranslations("recruitment.actions");
  const validation = useTranslations("recruitment.validation");
  const errors = useApiErrorTranslator("recruitment.errors");
  const cancelInterview = useInterviewCancel();
  const [error, setError] = useState<string | null>(null);

  const form = useForm<CandidateDecisionValues>({
    resolver: zodResolver(candidateDecisionSchema((key) => validation(key))),
    defaultValues: { note: "" },
  });

  async function onSubmit(values: CandidateDecisionValues) {
    setError(null);
    try {
      await cancelInterview.mutateAsync({
        interviewId: interview.id,
        data: { note: values.note === "" ? null : values.note },
      });
      onOpenChange(false);
    } catch (failure) {
      setError(recruitmentErrorMessage(failure, errors));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>
            {t("description", {
              name: candidateName ?? "",
              time: formatRecruitmentTimestamp(interview.scheduled_at),
            })}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
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

            {error ? (
              <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {error}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {common("keepInterview")}
              </Button>
              <Button type="submit" variant="destructive" disabled={cancelInterview.isPending}>
                {cancelInterview.isPending ? t("cancelling") : t("cancelAction")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
