"use client";

/**
 * Add a per-run, per-employee bonus. Bonuses are inputs, not immediate pay:
 * the dialog states that a recalculation is needed, and the mutation toast
 * repeats it. Only employees with a payslip in the run are offered, matching
 * the API's `payroll_employee_not_in_run` guard. Client leaf.
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { employeeLabel } from "../format";
import {
  bonusFieldErrors,
  useRunBonusCreate,
  type BonusFieldName,
} from "../hooks/use-payroll-runs";
import { bonusFormSchema, type BonusFormValues } from "../schema";
import type { Payslip } from "../types";

type BonusDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  runId: string;
  /** The run's payslips: the employee options the API will accept. */
  payslips: Payslip[];
  currency: string | null;
  directory: ReadonlyMap<string, string>;
};

const EMPTY_BONUS = {
  employee_id: "",
  amount: undefined,
  note: "",
};

export function BonusDialog({
  open,
  onOpenChange,
  runId,
  payslips,
  currency,
  directory,
}: BonusDialogProps) {
  const t = useTranslations("payroll.bonus");
  const root = useTranslations("payroll");
  const validation = useTranslations("payroll.validation");
  const errors = useTranslations("payroll.errors");
  const [rootError, setRootError] = useState<string | null>(null);
  const createBonus = useRunBonusCreate(runId);

  const form = useForm<BonusFormValues>({
    resolver: zodResolver(bonusFormSchema((key) => validation(key))),
    defaultValues: EMPTY_BONUS,
  });

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      form.reset(EMPTY_BONUS);
      setRootError(null);
    }
    onOpenChange(nextOpen);
  }

  async function onSubmit(values: BonusFormValues) {
    setRootError(null);
    try {
      await createBonus.mutateAsync({
        id: runId,
        data: {
          employee_id: values.employee_id,
          amount: values.amount,
          note: values.note?.trim() ? values.note.trim() : null,
        },
      });
      handleOpenChange(false);
    } catch (error) {
      const fieldErrors = bonusFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<BonusFieldName, "root">, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        {payslips.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("noPayslips")}</p>
        ) : (
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
              <FormField
                control={form.control}
                name="employee_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("employee")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger aria-label={t("employee")}>
                          <SelectValue placeholder={t("selectEmployee")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {payslips.map((payslip) => (
                          <SelectItem key={payslip.id} value={payslip.employee_id}>
                            {employeeLabel(
                              payslip.employee_id,
                              directory,
                              root("employeeFallback", { id: payslip.employee_id.slice(-4) }),
                            )}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <FormField
                  control={form.control}
                  name="amount"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel
                        hint={
                          currency === null
                            ? t("amountHint")
                            : t("amountHintCurrency", { currency })
                        }
                      >
                        {t("amount")}
                      </FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          value={field.value ?? ""}
                          type="number"
                          min={0.01}
                          step="0.01"
                          inputMode="decimal"
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
                  name="note"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint={t("noteHint")}>{t("note")}</FormLabel>
                      <FormControl>
                        <Textarea
                          {...field}
                          value={field.value ?? ""}
                          rows={2}
                          placeholder={t("notePlaceholder")}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

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
                <Button type="submit" disabled={createBonus.isPending}>
                  {t("add")}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  );
}
