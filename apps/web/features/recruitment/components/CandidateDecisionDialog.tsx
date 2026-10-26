"use client";

/**
 * Reject / withdraw confirmation for a candidate. Both side exits are terminal
 * and record an optional note (PII — visible to HR only, never in events); the
 * server's pinned `409 candidate_not_open` renders in place when the row moved
 * underneath the dialog. Client leaf.
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

import { recruitmentErrorMessage } from "../labels";
import { useCandidateReject, useCandidateWithdraw } from "../hooks/use-recruitment";
import { candidateDecisionSchema, type CandidateDecisionValues } from "../schema";
import type { Candidate } from "../types";

export type CandidateDecisionAction = "reject" | "withdraw";

type CandidateDecisionDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidate: Candidate;
  action: CandidateDecisionAction;
};

export function CandidateDecisionDialog({
  open,
  onOpenChange,
  candidate,
  action,
}: CandidateDecisionDialogProps) {
  const t = useTranslations("recruitment.candidateDecision");
  const common = useTranslations("recruitment.actions");
  const validation = useTranslations("recruitment.validation");
  const errors = useTranslations("recruitment.errors");
  const rejectCandidate = useCandidateReject();
  const withdrawCandidate = useCandidateWithdraw();
  const mutation = action === "reject" ? rejectCandidate : withdrawCandidate;
  const [error, setError] = useState<string | null>(null);

  const form = useForm<CandidateDecisionValues>({
    resolver: zodResolver(candidateDecisionSchema((key) => validation(key))),
    defaultValues: { note: "" },
  });

  const name = `${candidate.first_name} ${candidate.last_name}`;
  const isReject = action === "reject";

  async function onSubmit(values: CandidateDecisionValues) {
    setError(null);
    const data = { note: values.note === "" ? null : values.note };

    try {
      if (isReject) {
        await rejectCandidate.mutateAsync({ candidateId: candidate.id, data });
      } else {
        await withdrawCandidate.mutateAsync({ candidateId: candidate.id, data });
      }
      onOpenChange(false);
    } catch (failure) {
      setError(recruitmentErrorMessage(failure, errors));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isReject ? t("rejectTitle") : t("withdrawTitle")}</DialogTitle>
          <DialogDescription>
            {isReject
              ? t("rejectDescription", { name })
              : t("withdrawDescription", { name })}
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
                      rows={4}
                      placeholder={
                        isReject ? t("rejectNotePlaceholder") : t("withdrawNotePlaceholder")
                      }
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
              <Button
                type="submit"
                variant={isReject ? "destructive" : "default"}
                disabled={mutation.isPending}
              >
                {mutation.isPending
                  ? isReject
                    ? t("rejecting")
                    : t("withdrawing")
                  : isReject
                    ? t("rejectAction")
                    : t("withdrawAction")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
