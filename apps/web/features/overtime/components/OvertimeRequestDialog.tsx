"use client";

/**
 * Submit an overtime claim — one work date, decimal hours and an optional
 * reason (PII: visible only to the requester and deciders). Server errors map
 * back to the field that caused them (`409 overtime_work_date_in_future` /
 * `overtime_overlaps` → work date) and the dialog stays open so the claim can
 * be corrected. Client leaf.
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
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import { todayISO } from "../dates";
import {
  overtimeFieldErrors,
  useCreateOvertimeAction,
  type OvertimeFieldName,
} from "../hooks/use-overtime";
import { overtimeRequestFormSchema, type OvertimeRequestFormValues } from "../schema";

const EMPTY_VALUES: OvertimeRequestFormValues = {
  work_date: "",
  hours: "",
  reason: "",
};

type OvertimeRequestDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function OvertimeRequestDialog({ open, onOpenChange }: OvertimeRequestDialogProps) {
  const t = useTranslations("overtime.request");
  const common = useTranslations("common.actions");
  const validation = useTranslations("overtime.validation");
  const errors = useTranslations("overtime.errors");
  const createRequest = useCreateOvertimeAction();
  const [rootError, setRootError] = useState<string | null>(null);

  // The caller mounts the dialog fresh for every claim, so the empty defaults
  // are the initial state.
  const form = useForm<OvertimeRequestFormValues>({
    resolver: zodResolver(overtimeRequestFormSchema((key) => validation(key))),
    defaultValues: EMPTY_VALUES,
  });

  async function onSubmit(values: OvertimeRequestFormValues) {
    setRootError(null);
    try {
      await createRequest.mutateAsync({
        data: {
          work_date: values.work_date,
          hours: Number(values.hours),
          reason: values.reason?.trim() ? values.reason.trim() : null,
        },
      });
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = overtimeFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<OvertimeFieldName, "root">, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="work_date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("workDate")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="date" max={todayISO()} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="hours"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("hoursHint")}>{t("hours")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="number"
                        inputMode="decimal"
                        min="0.01"
                        max="12"
                        step="0.01"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="reason"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("reason")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value ?? ""}
                      rows={3}
                      placeholder={t("reasonPlaceholder")}
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
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={createRequest.isPending}>
                {createRequest.isPending ? t("submitting") : t("submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
