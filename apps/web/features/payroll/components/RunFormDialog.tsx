"use client";

/**
 * Start a run for a closed month. The create endpoint only queues it; the run
 * page then polls the worker's progress. Period errors (`not closed`, `open`)
 * stay on the field while the dialog remains open for a correction. Client
 * leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
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

import { useApiErrorTranslator } from "@/lib/api-errors";

import { lastClosedPeriod } from "../format";
import { runFieldErrors, usePayrollRunCreate, type RunFieldName } from "../hooks/use-payroll-runs";
import { runFormSchema, type RunFormValues } from "../schema";

type RunFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function RunFormDialog({ open, onOpenChange }: RunFormDialogProps) {
  const t = useTranslations("payroll.runForm");
  const common = useTranslations("common.actions");
  const validation = useTranslations("payroll.validation");
  const errors = useApiErrorTranslator("payroll.errors");
  const router = useRouter();
  const createRun = usePayrollRunCreate();
  const [rootError, setRootError] = useState<string | null>(null);

  const form = useForm<RunFormValues>({
    resolver: zodResolver(runFormSchema((key) => validation(key))),
    defaultValues: { period: lastClosedPeriod() },
  });

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      form.reset({ period: lastClosedPeriod() });
      setRootError(null);
    }
    onOpenChange(nextOpen);
  }

  async function onSubmit(values: RunFormValues) {
    setRootError(null);
    try {
      const response = await createRun.mutateAsync({ data: { period: values.period } });
      handleOpenChange(false);
      if (response.status === 201) {
        router.push(`/payroll/runs/${response.data.id}`);
      }
    } catch (error) {
      const fieldErrors = runFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<RunFieldName, "root">, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="period"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("monthHint")}>{t("month")}</FormLabel>
                  <FormControl>
                    <Input {...field} value={field.value} type="month" max={lastClosedPeriod()} />
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
              <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={createRun.isPending}>
                {createRun.isPending ? t("starting") : t("startRun")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
