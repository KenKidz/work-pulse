"use client";

/**
 * Feedback dialog — the assigned interviewer's completion step. Submitting
 * files the rating (1..5) and optional notes, moving the interview to
 * `completed`; the candidate's stage stays untouched (HR-driven). Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
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
  FormDescription,
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

import { formatRecruitmentTimestamp } from "../dates";
import { interviewRatingLabel, recruitmentErrorMessage } from "../labels";
import { useInterviewComplete } from "../hooks/use-recruitment";
import { interviewFeedbackSchema, type InterviewFeedbackValues } from "../schema";
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
  const completeInterview = useInterviewComplete();
  const [error, setError] = useState<string | null>(null);

  const form = useForm<InterviewFeedbackValues>({
    resolver: zodResolver(interviewFeedbackSchema),
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
      setError(recruitmentErrorMessage(failure));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Submit feedback</DialogTitle>
          <DialogDescription>
            {candidateName ?? "This candidate"} ·{" "}
            {formatRecruitmentTimestamp(interview.scheduled_at)}. Submitting completes the
            interview; the candidate&apos;s stage stays with HR.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="rating"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Rating</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Choose a rating" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {Object.entries(interviewRatingLabel).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
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
                  <FormLabel>Feedback notes (optional)</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value}
                      rows={5}
                      placeholder="What stood out, what to probe further and how the candidate compares."
                    />
                  </FormControl>
                  <FormDescription>
                    Sensitive: visible to HR and never included in events or audit values.
                  </FormDescription>
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
                Cancel
              </Button>
              <Button type="submit" disabled={completeInterview.isPending}>
                {completeInterview.isPending ? "Submitting…" : "Submit feedback"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
