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
import { Input } from "@/components/ui/input";

import { lastClosedPeriod } from "../format";
import { runFieldErrors, usePayrollRunCreate, type RunFieldName } from "../hooks/use-payroll-runs";
import { runFormSchema, type RunFormValues } from "../schema";

type RunFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function RunFormDialog({ open, onOpenChange }: RunFormDialogProps) {
  const router = useRouter();
  const createRun = usePayrollRunCreate();
  const [rootError, setRootError] = useState<string | null>(null);

  const form = useForm<RunFormValues>({
    resolver: zodResolver(runFormSchema),
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
      const fieldErrors = runFieldErrors(error);
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
          <DialogTitle>Start payroll run</DialogTitle>
          <DialogDescription>
            A run covers one whole calendar month and can only start after it has ended. Calculation
            happens in the background.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="period"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Month</FormLabel>
                  <FormControl>
                    <Input {...field} value={field.value} type="month" max={lastClosedPeriod()} />
                  </FormControl>
                  <FormDescription>Closed months only, in YYYY-MM form.</FormDescription>
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
                Cancel
              </Button>
              <Button type="submit" disabled={createRun.isPending}>
                {createRun.isPending ? "Starting…" : "Start run"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
