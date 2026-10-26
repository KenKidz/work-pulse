"use client";

/**
 * The shared decision dialog for both overtime steps — manager approve,
 * HR verify, and reject from either step. The server routes the optional note
 * to `manager_note` or `hr_note` from the observed source state; the dialog
 * only collects it. A `403` here is the server refusing self-decision and is
 * rendered in place, keeping the row context visible. Client leaf.
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
import { Textarea } from "@/components/ui/textarea";

import { formatDate, formatHours } from "../dates";
import {
  overtimeDecisionError,
  useApproveOvertimeAction,
  useRejectOvertimeAction,
  useVerifyOvertimeAction,
} from "../hooks/use-overtime";
import { overtimeDecisionFormSchema, type OvertimeDecisionFormValues } from "../schema";
import type { OvertimeRequest } from "../types";

export type OvertimeDecisionMode = "approve" | "verify" | "reject";

export type OvertimeDecisionTarget = {
  request: OvertimeRequest;
  mode: OvertimeDecisionMode;
};

export function DecisionDialog({
  target,
  employeeName,
  onClose,
}: {
  target: OvertimeDecisionTarget;
  employeeName: string;
  onClose: () => void;
}) {
  const t = useTranslations("overtime");
  const locale = useLocale();
  const common = useTranslations("common.actions");
  const errors = useTranslations("overtime.errors");
  const approve = useApproveOvertimeAction();
  const verify = useVerifyOvertimeAction();
  const reject = useRejectOvertimeAction();
  const mutation = target.mode === "approve" ? approve : target.mode === "verify" ? verify : reject;
  const [rootError, setRootError] = useState<string | null>(null);

  const form = useForm<OvertimeDecisionFormValues>({
    resolver: zodResolver(overtimeDecisionFormSchema((key) => t(`validation.${key}`))),
    defaultValues: { note: "" },
  });

  async function onSubmit(values: OvertimeDecisionFormValues) {
    setRootError(null);
    try {
      await mutation.mutateAsync({
        id: target.request.id,
        data: { note: values.note?.trim() ? values.note.trim() : null },
      });
      onClose();
    } catch (error) {
      setRootError(overtimeDecisionError(error, errors));
    }
  }

  const { request } = target;
  const description = t(
    target.mode === "verify"
      ? "decision.verifyDescription"
      : target.mode === "approve"
        ? "decision.approveDescription"
        : "decision.rejectDescription",
    {
      name: employeeName,
      date: formatDate(request.work_date, locale),
      hours: formatHours(request.hours, locale),
    },
  );

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{
            target.mode === "approve"
              ? t("decision.approveTitle")
              : target.mode === "verify"
                ? t("decision.verifyTitle")
                : t("decision.rejectTitle")
          }</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="note"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("decision.note")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value ?? ""}
                      rows={3}
                      placeholder={t("decision.notePlaceholder")}
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
              <Button type="button" variant="outline" onClick={onClose}>
                {common("cancel")}
              </Button>
              <Button
                type="submit"
                variant={target.mode === "reject" ? "destructive" : "default"}
                disabled={mutation.isPending}
              >
                {mutation.isPending
                  ? target.mode === "approve"
                    ? t("decision.approving")
                    : target.mode === "verify"
                      ? t("decision.verifying")
                      : t("decision.rejecting")
                  : target.mode === "approve"
                    ? t("decision.approveRequest")
                    : target.mode === "verify"
                      ? t("decision.verifyRequest")
                      : t("decision.rejectRequest")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
