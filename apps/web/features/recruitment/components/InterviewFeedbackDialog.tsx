"use client";

/**
 * Feedback dialog — the assigned interviewer's completion step. Submitting
 * files the rating (1..5) and optional notes, moving the interview to
 * `completed`; the candidate's stage stays untouched (HR-driven). Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
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

import { useApiErrorTranslator } from "@/lib/api-errors";

import { formatRecruitmentTimestamp } from "../dates";
import { recruitmentErrorMessage } from "../labels";
import { useInterviewComplete } from "../hooks/use-recruitment";
import {
  INTERVIEW_RATINGS,
  interviewFeedbackSchema,
  type InterviewFeedbackValues,
} from "../schema";
import type { Interview } from "../types";

type InterviewFeedbackDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  interview: Interview;
  candidateName?: string;
};

export function InterviewFeedbackDialog({
  open,
  onOpenChange,
  interview,
  candidateName,
}: InterviewFeedbackDialogProps) {
  const t = useTranslations("recruitment.feedback");
  const common = useTranslations("recruitment.actions");
  const validation = useTranslations("recruitment.validation");
  const errors = useApiErrorTranslator("recruitment.errors");
  const ratings = useTranslations("recruitment.ratings");
  const locale = useLocale();
  const completeInterview = useInterviewComplete();
  const [error, setError] = useState<string | null>(null);

  const form = useForm<InterviewFeedbackValues>({
    resolver: zodResolver(interviewFeedbackSchema((key) => validation(key))),
    defaultValues: { rating: "", notes: "" },
  });

  async function onSubmit(values: InterviewFeedbackValues) {
    setError(null);
    try {
      await completeInterview.mutateAsync({
        interviewId: interview.id,
        data: {
          rating: Number(values.rating),
          notes: values.notes === "" ? null : values.notes,
        },
      });
      onOpenChange(false);
    } catch (failure) {
      setError(recruitmentErrorMessage(failure, errors));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>
            {t("description", {
              name: candidateName ?? "",
              time: formatRecruitmentTimestamp(interview.scheduled_at, locale),
            })}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="rating"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("rating")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder={t("chooseRating")} />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {INTERVIEW_RATINGS.map((value) => (
                        <SelectItem key={value} value={value}>
                          {ratings("option", { value, label: ratings(value) })}
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
              name="notes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("notesHint")}>{t("notes")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value}
                      rows={5}
                      placeholder={t("notesPlaceholder")}
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
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={completeInterview.isPending}>
                {completeInterview.isPending ? t("submitting") : t("submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
