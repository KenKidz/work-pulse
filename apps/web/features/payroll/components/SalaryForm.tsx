"use client";

/**
 * Effective-dated, append-only salary records. The form appends one record per
 * employee and effective date; the history table below is the audit surface
 * Finance/HR read. Roles that may read the employee directory get a picker;
 * a Finance session (which may not read employees) enters the employee id the
 * same way the API targets it. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert, Coins, Plus } from "lucide-react";
import { useState } from "react";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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

import {
  employeeLabel,
  formatMoney,
  formatPayrollDate,
  formatPayrollTimestamp,
  todayISO,
} from "../format";
import { usePayrollDirectory } from "../hooks/use-payroll-directory";
import {
  salaryFieldErrors,
  usePayrollSalaries,
  useSalaryCreate,
  type SalaryFieldName,
} from "../hooks/use-payroll-salaries";
import { salaryFormSchema, type SalaryFormValues } from "../schema";
import { PayrollSectionHeader } from "./PayrollSectionHeader";
import { TablePagination } from "./TablePagination";

const SALARIES_SECTION_ID = "payroll-salaries-title";
const SALARY_PAGE_SIZE = 20;

const EMPTY_SALARY = {
  employee_id: "",
  amount: undefined,
  effective_from: todayISO(),
};

export function SalaryForm({ canEdit }: { canEdit: boolean }) {
  const [page, setPage] = useState(1);
  const [rootError, setRootError] = useState<string | null>(null);
  const salaryQuery = usePayrollSalaries({ page, page_size: SALARY_PAGE_SIZE });
  const createSalary = useSalaryCreate();
  const directory = usePayrollDirectory();

  const form = useForm<SalaryFormValues>({
    resolver: zodResolver(salaryFormSchema),
    defaultValues: EMPTY_SALARY,
  });

  const salaries = salaryQuery.data?.status === 200 ? salaryQuery.data.data.items : [];
  const pageInfo = salaryQuery.data?.status === 200 ? salaryQuery.data.data.page : null;

  async function onSubmit(values: SalaryFormValues) {
    setRootError(null);
    try {
      await createSalary.mutateAsync({
        data: {
          employee_id: values.employee_id,
          amount: values.amount,
          effective_from: values.effective_from,
        },
      });
      setPage(1);
      form.reset({ ...EMPTY_SALARY, effective_from: values.effective_from });
    } catch (error) {
      const fieldErrors = salaryFieldErrors(error);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<SalaryFieldName, "root">, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <section aria-labelledby={SALARIES_SECTION_ID} className="flex flex-col gap-4">
      <PayrollSectionHeader
        id={SALARIES_SECTION_ID}
        title="Salary records"
        description="Append-only effective dates. A run uses the record effective on its last day; a new record never rewrites history."
      />

      {canEdit ? (
        <Card className="gap-0 rounded-md border-border py-0 shadow-none">
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onSubmit)}
              className="space-y-4 p-4 sm:p-5"
              noValidate
            >
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <FormField
                  control={form.control}
                  name="employee_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel
                        hint={
                          directory.canRead
                            ? "Names come from the employee directory."
                            : "Paste the employee’s UUID from the employee record."
                        }
                      >
                        {directory.canRead ? "Employee" : "Employee ID"}
                      </FormLabel>
                      {directory.canRead ? (
                        <Select value={field.value} onValueChange={field.onChange}>
                          <FormControl>
                            <SelectTrigger
                              disabled={directory.employees.length === 0}
                              aria-label="Employee"
                            >
                              <SelectValue placeholder="Select an employee" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {directory.employees.map((employee) => (
                              <SelectItem key={employee.id} value={employee.id}>
                                {`${employee.first_name} ${employee.last_name}`.trim()} ·{" "}
                                {employee.employee_code}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <FormControl>
                          <Input
                            {...field}
                            value={field.value}
                            placeholder="00000000-0000-4000-8000-000000000000"
                            autoComplete="off"
                          />
                        </FormControl>
                      )}
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="amount"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint="In the tenant currency. No proration on runs.">
                        Monthly base salary
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
                  name="effective_from"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint="One record per employee and date.">Effective from</FormLabel>
                      <FormControl>
                        <Input {...field} value={field.value} type="date" />
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

              <div className="flex justify-end">
                <Button type="submit" disabled={createSalary.isPending}>
                  <Plus />
                  Add salary record
                </Button>
              </div>
            </form>
          </Form>
        </Card>
      ) : null}

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {salaryQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading salary records…</p>
          </div>
        ) : salaryQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load salary records.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void salaryQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : salaries.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Coins}
              message="No salary records yet. Add one for each employee before running payroll."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every salary field.
            </p>
            <Table
              aria-label="Salary records"
              containerLabel="Salary records. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Employee</TableHead>
                  <TableHead className="text-right">Base salary</TableHead>
                  <TableHead>Effective from</TableHead>
                  <TableHead>Recorded</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {salaries.map((salary) => (
                  <TableRow key={salary.id}>
                    <TableCell className="pl-3 font-medium">
                      {employeeLabel(salary.employee_id, directory.nameById)}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {formatMoney(salary.amount, salary.currency)}
                    </TableCell>
                    <TableCell>{formatPayrollDate(salary.effective_from)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      <time dateTime={salary.created_at}>
                        {formatPayrollTimestamp(salary.created_at)}
                      </time>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {pageInfo === null ? null : (
              <TablePagination page={pageInfo} label="salary records" onPageChange={setPage} />
            )}
          </>
        )}
      </Card>
    </section>
  );
}
