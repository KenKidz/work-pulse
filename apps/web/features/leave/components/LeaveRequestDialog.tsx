"use client";

/**
 * Submit a leave request — whole calendar days, seeded leave type, optional
 * reason (PII: visible only to the requester and deciders). Server errors map
 * back to the field that caused them (`409 leave_overlaps` → start date) and
 * the dialog stays open so the request can be corrected (AC12 pattern).
 * Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { earliestStartDate, inclusiveDays } from "../dates";
import {
  leaveFieldErrors,
  useCreateLeaveAction,
  useLeaveTypes,
  type LeaveFieldName,
} from "../hooks/use-leave";
import { leaveRequestFormSchema, type LeaveRequestFormValues } from "../schema";

const EMPTY_VALUES: LeaveRequestFormValues = {
  leave_type_id: "",
  start_date: "",
  end_date: "",
  reason: "",
};

type LeaveRequestDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function LeaveRequestDialog({ open, onOpenChange }: LeaveRequestDialogProps) {
  const t = useTranslations("leave.request");
  const common = useTranslations("common.actions");
  const validation = useTranslations("leave.validation");
  const errors = useTranslations("leave.errors");
  const typesQuery = useLeaveTypes();
  const createRequest = useCreateLeaveAction();
  const [rootError, setRootError] = useState<string | null>(null);

  // The dialog is mounted fresh for every request (caller renders it only
  // while open), so the empty defaults are the initial state.
  const form = useForm<LeaveRequestFormValues>({
    resolver: zodResolver(leaveRequestFormSchema((key) => validation(key))),
    defaultValues: EMPTY_VALUES,
  });

  const types = typesQuery.data?.status === 200 ? typesQuery.data.data : [];
  const selectedTypeId = useWatch({ control: form.control, name: "leave_type_id" });
  const selectedType = types.find((type) => type.id === selectedTypeId);
  const startDate = useWatch({ control: form.control, name: "start_date" });
  const endDate = useWatch({ control: form.control, name: "end_date" });
  const days = inclusiveDays(startDate, endDate);
  const earliestStart = earliestStartDate(selectedType?.code);

  async function onSubmit(values: LeaveRequestFormValues) {
    setRootError(null);
    try {
      await createRequest.mutateAsync({
        data: {
          leave_type_id: values.leave_type_id,
          start_date: values.start_date,
          end_date: values.end_date,
          reason: values.reason?.trim() ? values.reason.trim() : null,
        },
      });
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = leaveFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<LeaveFieldName, "root">, {
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
            <FormField
              control={form.control}
              name="leave_type_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("leaveType")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger disabled={typesQuery.isPending}>
                        <SelectValue
                          placeholder={
                            typesQuery.isPending ? t("loadingTypes") : t("selectType")
                          }
                        />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {types.map((type) => (
                        <SelectItem key={type.id} value={type.id}>
                          {type.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            {selectedType && !selectedType.counts_against_balance ? (
              <p className="-mt-2 text-xs text-muted-foreground">
                {t("noBalance")}
              </p>
            ) : null}

            {typesQuery.isError ? (
              <div className="flex flex-wrap items-center gap-2 text-sm text-destructive">
                <CircleAlert className="size-4 shrink-0" aria-hidden="true" />
                <p role="alert">{t("loadTypesError")}</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void typesQuery.refetch()}
                >
                  {common("retry")}
                </Button>
              </div>
            ) : null}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="start_date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("startDate")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="date" min={earliestStart} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="end_date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("endDate")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="date" min={startDate || earliestStart} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            {days !== null ? (
              <p className="text-xs text-muted-foreground">
                {t("inclusiveDays", { days })}
              </p>
            ) : null}

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
