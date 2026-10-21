"use client";

/**
 * Tenant deduction lines — the transparent rules every payslip itemises. The
 * list is unpaged by contract and ordered by code (the same order payslips
 * use). Operators create/edit; HR sees the same table read-only. Client leaf.
 */

import { CircleAlert, Pencil, Plus, Receipt } from "lucide-react";
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
import { deductionKindLabel } from "../labels";
import type { PayrollDeduction } from "../types";
import { DeductionFormDialog } from "./DeductionFormDialog";
import { PayrollSectionHeader } from "./PayrollSectionHeader";

const DEDUCTIONS_SECTION_ID = "payroll-deductions-title";

export function DeductionLinesTable({ canEdit }: { canEdit: boolean }) {
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
        title="Deduction lines"
        description="Tenant-wide rules, itemised on every payslip. Inactive lines are kept for history and appear on no payslip."
      >
        {canEdit ? (
          <Button onClick={openCreate}>
            <Plus />
            New deduction line
          </Button>
        ) : null}
      </PayrollSectionHeader>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {deductionsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading deduction lines…</p>
          </div>
        ) : deductionsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load deduction lines.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void deductionsQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : deductions.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Receipt}
              message="No deduction lines yet. Add the rules that should itemise on every payslip."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every deduction field.
            </p>
            <Table
              aria-label="Deduction lines"
              containerLabel="Deduction lines. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Code</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                  <TableHead>Status</TableHead>
                  {canEdit ? <TableHead className="pr-3 text-right">Actions</TableHead> : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {deductions.map((deduction) => (
                  <TableRow key={deduction.id}>
                    <TableCell className="pl-3 font-mono text-xs">{deduction.code}</TableCell>
                    <TableCell className="font-medium">{deduction.name}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {deductionKindLabel[deduction.kind]}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {deduction.kind === "percent_of_base"
                        ? `${formatHours(deduction.value)}%`
                        : currency === null
                          ? formatHours(deduction.value)
                          : formatMoney(deduction.value, currency)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={deduction.active ? "success" : "muted"}>
                        {deduction.active ? "Active" : "Inactive"}
                      </Badge>
                    </TableCell>
                    {canEdit ? (
                      <TableCell className="pr-3 text-right">
                        <Button
                          variant="ghost"
                          size="xs"
                          className="min-h-11 sm:min-h-7"
                          aria-label={`Edit ${deduction.name}`}
                          onClick={() => openEdit(deduction)}
                        >
                          <Pencil />
                          Edit
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
