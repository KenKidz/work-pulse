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

const TITLES: Record<OvertimeDecisionMode, string> = {
  approve: "Approve overtime request",
  verify: "Verify overtime request",
  reject: "Reject overtime request",
};

const PENDING_LABELS: Record<OvertimeDecisionMode, string> = {
  approve: "Approving…",
  verify: "Verifying…",
  reject: "Rejecting…",
};

const SUBMIT_LABELS: Record<OvertimeDecisionMode, string> = {
  approve: "Approve request",
  verify: "Verify request",
  reject: "Reject request",
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
  const approve = useApproveOvertimeAction();
  const verify = useVerifyOvertimeAction();
  const reject = useRejectOvertimeAction();
  const mutation = target.mode === "approve" ? approve : target.mode === "verify" ? verify : reject;
  const [rootError, setRootError] = useState<string | null>(null);

  const form = useForm<OvertimeDecisionFormValues>({
    resolver: zodResolver(overtimeDecisionFormSchema),
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
      setRootError(overtimeDecisionError(error));
    }
  }

  const { request } = target;
  const description =
    target.mode === "verify"
      ? `${employeeName} · ${formatDate(request.work_date)} · ${formatHours(request.hours)} hours. Payroll counts these hours exactly as requested; HR cannot adjust them.`
      : target.mode === "approve"
        ? `${employeeName} · ${formatDate(request.work_date)} · ${formatHours(request.hours)} hours. The request then waits for HR verification.`
        : `${employeeName} · ${formatDate(request.work_date)} · ${formatHours(request.hours)} hours. The requester must submit a new claim.`;

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{TITLES[target.mode]}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="note"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Decision note (optional)</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value ?? ""}
                      rows={3}
                      placeholder="Explain the decision for the requester."
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
                Cancel
              </Button>
              <Button
                type="submit"
                variant={target.mode === "reject" ? "destructive" : "default"}
                disabled={mutation.isPending}
              >
                {mutation.isPending ? PENDING_LABELS[target.mode] : SUBMIT_LABELS[target.mode]}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
