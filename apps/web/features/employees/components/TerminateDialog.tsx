"use client";

/**
 * Termination dialog — a confirm-style form. Termination is terminal: it
 * closes the open employment period at `effective_date` and the status
 * becomes `terminated` for good. The reason goes to the audit trail only.
 * Server errors are rendered as a visible root error (AC12). Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
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
import { Textarea } from "@/components/ui/textarea";

import { useApiErrorTranslator } from "@/lib/api-errors";

import { employeeFieldErrors, errorMessage, useEmployeeTerminate } from "../hooks/use-employees";
import { terminationFormSchema, type TerminationFormValues } from "../schema";
import { todayISO } from "./dates";

type TerminateDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employeeId: string;
};

export function TerminateDialog({ open, onOpenChange, employeeId }: TerminateDialogProps) {
  const t = useTranslations("employees");
  const common = useTranslations("common.actions");
  const errors = useApiErrorTranslator("employees.errors");
  const [rootError, setRootError] = useState<string | null>(null);
  const terminate = useEmployeeTerminate();

  const form = useForm<TerminationFormValues>({
    resolver: zodResolver(terminationFormSchema((key) => t(`terminate.validation.${key}`))),
    defaultValues: {
      effective_date: todayISO(),
      reason: "",
    },
  });

  async function onSubmit(values: TerminationFormValues) {
    setRootError(null);
    try {
      await terminate.mutateAsync({
        id: employeeId,
        data: {
          effective_date: values.effective_date,
          reason: values.reason,
        },
      });
      onOpenChange(false);
    } catch (error) {
      // Termination errors are root-level: the record is closed, nothing to
      // fix per-field.
      setRootError(employeeFieldErrors(error, errors).root ?? errorMessage(error, errors));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("terminate.title")}</DialogTitle>
          <DialogDescription>{t("terminate.description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="effective_date"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("terminate.effectiveDate")}</FormLabel>
                  <FormControl>
                    <Input {...field} type="date" max={todayISO()} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="reason"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("terminate.reason")}</FormLabel>
                  <FormControl>
                    <Textarea {...field} rows={3} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {rootError ? (
              <p role="alert" className="text-sm text-destructive">
                {rootError}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" variant="destructive" disabled={terminate.isPending}>
                {t("terminate.confirm")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
