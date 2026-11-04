"use client";

/**
 * Tenant payroll configuration — one currency, standard monthly hours and the
 * overtime multiplier. Finance/Admin/Super Admin edit; HR sees the same values
 * read-only. The helper sentence spells out the formula so the simulation is
 * inspectable (ADR 0003), not a black box. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";

import { useApiErrorTranslator } from "@/lib/api-errors";

import { formatPayrollTimestamp } from "../format";
import { payrollErrorMessage } from "../errors";
import { usePayrollConfig, usePayrollConfigUpdate } from "../hooks/use-payroll-config";
import { payrollConfigFormSchema, type PayrollConfigFormValues } from "../schema";
import { PayrollSectionHeader } from "./PayrollSectionHeader";

const CONFIG_SECTION_ID = "payroll-config-title";

export function PayrollConfigForm({ canEdit }: { canEdit: boolean }) {
  const t = useTranslations("payroll.config");
  const common = useTranslations("common.actions");
  const validation = useTranslations("payroll.validation");
  const errors = useApiErrorTranslator("payroll.errors");
  const locale = useLocale();
  const configQuery = usePayrollConfig();
  const updateConfig = usePayrollConfigUpdate();
  const [rootError, setRootError] = useState<string | null>(null);

  const config = configQuery.data?.status === 200 ? configQuery.data.data : null;

  const form = useForm<PayrollConfigFormValues>({
    resolver: zodResolver(
      payrollConfigFormSchema(validation, {
        standardMonthlyHours: t("standardHours"),
        overtimeMultiplier: t("overtimeMultiplier"),
      }),
    ),
    defaultValues: {
      currency: "",
      standard_monthly_hours: 160,
      overtime_multiplier: 1.5,
    },
  });

  useEffect(() => {
    if (config === null) return;
    form.reset({
      currency: config.currency,
      standard_monthly_hours: config.standard_monthly_hours,
      overtime_multiplier: config.overtime_multiplier,
    });
  }, [config, form]);

  async function onSubmit(values: PayrollConfigFormValues) {
    if (!canEdit) return;
    setRootError(null);
    try {
      await updateConfig.mutateAsync({ data: values });
    } catch (error) {
      setRootError(payrollErrorMessage(error, errors));
    }
  }

  return (
    <section aria-labelledby={CONFIG_SECTION_ID} className="flex flex-col gap-4">
      <PayrollSectionHeader
        id={CONFIG_SECTION_ID}
        title={t("title")}
        description={t("description")}
      />

      <Card className="gap-0 rounded-md border-border py-0 shadow-none">
        {configQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("loading")}</p>
          </div>
        ) : configQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("loadError")}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void configQuery.refetch()}
                >
                  {common("retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : (
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onSubmit)}
              className="space-y-4 p-4 sm:p-5"
              noValidate
            >
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <FormField
                  control={form.control}
                  name="currency"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint={t("currencyHint")}>{t("currency")}</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          value={field.value}
                          maxLength={3}
                          autoComplete="off"
                          autoCapitalize="characters"
                          placeholder="USD"
                          readOnly={!canEdit}
                          disabled={updateConfig.isPending}
                          onChange={(event) => field.onChange(event.target.value.toUpperCase())}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="standard_monthly_hours"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint={t("standardHoursHint")}>{t("standardHours")}</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          value={field.value ?? ""}
                          type="number"
                          min={0.01}
                          step="0.01"
                          inputMode="decimal"
                          readOnly={!canEdit}
                          disabled={updateConfig.isPending}
                          onChange={(event) =>
                            field.onChange(
                              event.target.value === "" ? undefined : event.target.value,
                            )
                          }
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="overtime_multiplier"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint={t("overtimeMultiplierHint")}>
                        {t("overtimeMultiplier")}
                      </FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          value={field.value ?? ""}
                          type="number"
                          min={0.01}
                          step="0.01"
                          inputMode="decimal"
                          readOnly={!canEdit}
                          disabled={updateConfig.isPending}
                          onChange={(event) =>
                            field.onChange(
                              event.target.value === "" ? undefined : event.target.value,
                            )
                          }
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
                {t("formula")}
              </p>

              {rootError ? (
                <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
                  <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  {rootError}
                </p>
              ) : null}

              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground">
                  {config === null
                    ? null
                    : t("lastUpdated", {
                        time: formatPayrollTimestamp(config.updated_at, locale),
                      })}
                </p>
                {canEdit ? (
                  <Button type="submit" disabled={updateConfig.isPending}>
                    {t("saveChanges")}
                  </Button>
                ) : (
                  <p className="text-xs text-muted-foreground">{t("viewOnly")}</p>
                )}
              </div>
            </form>
          </Form>
        )}
      </Card>
    </section>
  );
}
