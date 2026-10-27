"use client";

/**
 * The nominated provider's one feedback comment. Submission is final: a second
 * attempt is refused by the server with `feedback_already_submitted`, rendered
 * here with the pinned sentence while the typed text stays put. Client leaf.
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
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { performanceErrorMessage } from "../labels";
import { useReviewFeedbackSubmit } from "../hooks/use-performance";
import { feedbackFormSchema, type FeedbackFormValues } from "../schema";
import type { Review } from "../types";

type FeedbackDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  review: Review;
  employeeName: string;
};

export function FeedbackDialog({ open, onOpenChange, review, employeeName }: FeedbackDialogProps) {
  const t = useTranslations("performance.feedback.form");
  const common = useTranslations("performance.actions");
  const validation = useTranslations("performance.validation");
  const errors = useTranslations("performance.errors");
  const submitFeedback = useReviewFeedbackSubmit();
  const [error, setError] = useState<string | null>(null);

  const form = useForm<FeedbackFormValues>({
    resolver: zodResolver(feedbackFormSchema((key) => validation(key))),
    defaultValues: { comment: "" },
  });

  async function onSubmit(values: FeedbackFormValues) {
    setError(null);
    try {
      await submitFeedback.mutateAsync({
        id: review.id,
        data: { comment: values.comment.trim() },
      });
      onOpenChange(false);
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 403
          ? t("denied")
          : performanceErrorMessage(failure, errors),
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description", { name: employeeName })}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="comment"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("commentHint")}>{t("comment")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value}
                      rows={5}
                      placeholder={t("commentPlaceholder")}
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
              <Button type="submit" disabled={submitFeedback.isPending}>
                {submitFeedback.isPending ? t("submitting") : t("submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
