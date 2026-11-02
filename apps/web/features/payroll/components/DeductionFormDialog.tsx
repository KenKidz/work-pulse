"use client";

/**
 * Create/edit one transparent deduction line. `code` is the stable line key
 * and becomes read-only once created; the kind/value pair is validated here
 * and again by the API. Server errors map back to the field that caused them
 * so the dialog can stay open for a correction. Client leaf.
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

import { useApiErrorTranslator } from "@/lib/api-errors";

import {
  deductionFieldErrors,
  usePayrollDeductionCreate,
  usePayrollDeductionUpdate,
  type DeductionFieldName,
} from "../hooks/use-payroll-deductions";
import { DEDUCTION_KINDS } from "../labels";
import { deductionFormSchema, type DeductionFormValues } from "../schema";
import type { PayrollDeduction } from "../types";

type DeductionFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a line = edit mode. */
  deduction: PayrollDeduction | null;
};

export function DeductionFormDialog({ open, onOpenChange, deduction }: DeductionFormDialogProps) {
  const t = useTranslations("payroll.deductionForm");
  const common = useTranslations("common.actions");
  const validation = useTranslations("payroll.validation");
  const errors = useApiErrorTranslator("payroll.errors");
  const kinds = useTranslations("payroll.kinds");
  const deductions = useTranslations("payroll.deductions");
  const isEdit = deduction !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createDeduction = usePayrollDeductionCreate();
  const updateDeduction = usePayrollDeductionUpdate();
  const mutation = isEdit ? updateDeduction : createDeduction;

  const defaults: DeductionFormValues = {
    code: deduction?.code ?? "",
    name: deduction?.name ?? "",
    kind: deduction?.kind ?? "percent_of_base",
    value: deduction?.value ?? 0.01,
    active: deduction?.active ?? true,
  };

  const form = useForm<DeductionFormValues>({
    resolver: zodResolver(deductionFormSchema((key) => validation(key))),
    defaultValues: defaults,
  });

  const kind = useWatch({ control: form.control, name: "kind" });

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      form.reset(defaults);
      setRootError(null);
    }
    onOpenChange(nextOpen);
  }

  async function onSubmit(values: DeductionFormValues) {
    setRootError(null);
    const payload = {
      name: values.name.trim(),
      kind: values.kind,
      value: values.value,
      active: values.active,
    };

    try {
      if (deduction !== null) {
        await updateDeduction.mutateAsync({ id: deduction.id, data: payload });
      } else {
        await createDeduction.mutateAsync({
          data: { ...payload, code: values.code.trim() },
        });
      }
      handleOpenChange(false);
    } catch (error) {
      const fieldErrors = deductionFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<DeductionFieldName, "root">, {
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
          <DialogTitle>{isEdit ? t("editTitle") : t("createTitle")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={isEdit ? t("codeHintEdit") : t("codeHintCreate")}>
                      {t("code")}
                    </FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value}
                        placeholder={t("codePlaceholder")}
                        autoComplete="off"
                        disabled={isEdit}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("nameHint")}>{t("name")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} placeholder={t("namePlaceholder")} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="kind"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("kind")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("chooseKind")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {DEDUCTION_KINDS.map((value) => (
                          <SelectItem key={value} value={value}>
                            {kinds(value)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="value"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel
                      hint={
                        kind === "percent_of_base" ? t("valuePercentHint") : t("valueFixedHint")
                      }
                    >
                      {t("value")}
                    </FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        type="number"
                        min={0.01}
                        max={kind === "percent_of_base" ? 100 : undefined}
                        step="0.01"
                        inputMode="decimal"
                        onChange={(event) =>
                          field.onChange(event.target.value === "" ? undefined : event.target.value)
                        }
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="active"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("statusHint")}>{t("status")}</FormLabel>
                  <Select
                    value={field.value ? "active" : "inactive"}
                    onValueChange={(value) => field.onChange(value === "active")}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder={t("chooseStatus")} />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="active">{deductions("active")}</SelectItem>
                      <SelectItem value="inactive">{deductions("inactive")}</SelectItem>
                    </SelectContent>
                  </Select>
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
              <Button type="submit" disabled={mutation.isPending}>
                {isEdit ? t("saveChanges") : t("create")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
