"use client";

/**
 * Tenant deduction lines — the transparent rules every payslip itemises. The
 * list is unpaged by contract and ordered by code (the same order payslips
 * use). Operators create/edit; HR sees the same table read-only. Client leaf.
 */

import { CircleAlert, Pencil, Plus, Receipt } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { formatHours, formatMoney } from "../format";
import { usePayrollConfig } from "../hooks/use-payroll-config";
import { usePayrollDeductions } from "../hooks/use-payroll-deductions";

import type { PayrollDeduction } from "../types";
import { DeductionFormDialog } from "./DeductionFormDialog";
import { PayrollSectionHeader } from "./PayrollSectionHeader";

const DEDUCTIONS_SECTION_ID = "payroll-deductions-title";

export function DeductionLinesTable({ canEdit }: { canEdit: boolean }) {
  const t = useTranslations("payroll.deductions");
  const common = useTranslations("common.actions");
  const kinds = useTranslations("payroll.kinds");
  const locale = useLocale();
  const deductionsQuery = usePayrollDeductions();
  const configQuery = usePayrollConfig();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<PayrollDeduction | null>(null);

  const deductions = deductionsQuery.data?.status === 200 ? deductionsQuery.data.data : [];
  const currency = configQuery.data?.status === 200 ? configQuery.data.data.currency : null;

  function openCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function openEdit(deduction: PayrollDeduction) {
    setEditing(deduction);
    setDialogOpen(true);
  }

  return (
    <section aria-labelledby={DEDUCTIONS_SECTION_ID} className="flex flex-col gap-4">
      <PayrollSectionHeader
        id={DEDUCTIONS_SECTION_ID}
        title={t("title")}
        description={t("description")}
      >
        {canEdit ? (
          <Button onClick={openCreate}>
            <Plus />
            {t("new")}
          </Button>
        ) : null}
      </PayrollSectionHeader>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {deductionsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("loading")}</p>
          </div>
        ) : deductionsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("loadError")}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void deductionsQuery.refetch()}
                >
                  {common("retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : deductions.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Receipt}
              message={t("empty")}
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              {t("swipe")}
            </p>
            <Table
              aria-label={t("label")}
              containerLabel={t("containerLabel")}
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("columns.code")}</TableHead>
                  <TableHead>{t("columns.name")}</TableHead>
                  <TableHead>{t("columns.kind")}</TableHead>
                  <TableHead className="text-right">{t("columns.value")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  {canEdit ? <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead> : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {deductions.map((deduction) => (
                  <TableRow key={deduction.id}>
                    <TableCell className="pl-3 font-mono text-xs">{deduction.code}</TableCell>
                    <TableCell className="font-medium">{deduction.name}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {kinds(deduction.kind)}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {deduction.kind === "percent_of_base"
                        ? `${formatHours(deduction.value, locale)}%`
                        : currency === null
                          ? formatHours(deduction.value, locale)
                          : formatMoney(deduction.value, currency, locale)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={deduction.active ? "success" : "muted"}>
                        {deduction.active ? t("active") : t("inactive")}
                      </Badge>
                    </TableCell>
                    {canEdit ? (
                      <TableCell className="pr-3 text-right">
                        <Button
                          variant="ghost"
                          size="xs"
                          className="min-h-11 sm:min-h-7"
                          aria-label={t("editAria", { name: deduction.name })}
                          onClick={() => openEdit(deduction)}
                        >
                          <Pencil />
                          {t("edit")}
                        </Button>
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </Card>

      <DeductionFormDialog
        key={editing?.id ?? "new"}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        deduction={editing}
      />
    </section>
  );
}
