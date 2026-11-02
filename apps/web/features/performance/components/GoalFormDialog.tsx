"use client";

/**
 * Create/edit dialog for a goal — RHF + zod against the pinned
 * `GoalCreate`/`GoalUpdate` shapes. A goal is always created as `draft` and
 * activated separately. The subject can be chosen only on create and only by a
 * role with team/tenant standing; editing never moves a goal between people.
 * A failed save keeps the dialog and the user's input, with the mapped server
 * sentence at the root. Client leaf.
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

import { useApiErrorTranslator } from "@/lib/api-errors";

import {
  goalFieldErrors,
  useGoalCreate,
  useGoalUpdate,
  usePerformanceAccess,
  usePerformanceDirectory,
  type GoalFieldName,
} from "../hooks/use-performance";
import { goalFormSchema, SELF_EMPLOYEE, type GoalFormValues } from "../schema";
import type { Goal } from "../types";

type GoalFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a goal = edit (draft/active only). */
  goal: Goal | null;
};

export function GoalFormDialog({ open, onOpenChange, goal }: GoalFormDialogProps) {
  const t = useTranslations("performance.goalForm");
  const common = useTranslations("performance.actions");
  const validation = useTranslations("performance.validation");
  const errors = useApiErrorTranslator("performance.errors");
  const { user, canViewTeam } = usePerformanceAccess();
  const directory = usePerformanceDirectory();
  const isEdit = goal !== null;
  const canChooseSubject = canViewTeam && !isEdit;
  const hasSelfLink = user.employee_id != null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createGoal = useGoalCreate();
  const updateGoal = useGoalUpdate();
  const mutation = isEdit ? updateGoal : createGoal;

  const form = useForm<GoalFormValues>({
    resolver: zodResolver(goalFormSchema((key) => validation(key))),
    defaultValues: {
      employee_id: isEdit ? goal.employee_id : hasSelfLink ? SELF_EMPLOYEE : "",
      title: goal?.title ?? "",
      description: goal?.description ?? "",
      due_date: goal?.due_date ?? "",
      progress_pct: goal?.progress_pct ?? 0,
    },
  });

  const subjectOptions = directory.pickerEmployees.filter(
    (employee) => !hasSelfLink || employee.id !== user.employee_id,
  );

  async function onSubmit(values: GoalFormValues) {
    setRootError(null);
    const data = {
      title: values.title.trim(),
      description: values.description.trim() === "" ? null : values.description.trim(),
      due_date: values.due_date.trim() === "" ? null : values.due_date.trim(),
      progress_pct: values.progress_pct,
    };

    try {
      if (goal === null) {
        await createGoal.mutateAsync({
          data:
            canChooseSubject && values.employee_id !== SELF_EMPLOYEE
              ? { ...data, employee_id: values.employee_id }
              : data,
        });
      } else {
        await updateGoal.mutateAsync({ id: goal.id, data });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = goalFieldErrors(error, errors, {
        hasEmployeeField: canChooseSubject,
      });
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<GoalFieldName, "root">, {
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
          <DialogTitle>{isEdit ? t("editTitle") : t("createTitle")}</DialogTitle>
          <DialogDescription>
            {isEdit ? t("editDescription") : t("createDescription")}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {canChooseSubject ? (
              <FormField
                control={form.control}
                name="employee_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("employeeHint")}>{t("employee")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("selectEmployee")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {hasSelfLink ? (
                          <SelectItem value={SELF_EMPLOYEE}>{t("self")}</SelectItem>
                        ) : null}
                        {subjectOptions.map((employee) => (
                          <SelectItem key={employee.id} value={employee.id}>
                            {`${employee.first_name} ${employee.last_name}`.trim()}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}

            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("title")}</FormLabel>
                  <FormControl>
                    <Input {...field} value={field.value} placeholder={t("titlePlaceholder")} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("descriptionHint")}>{t("description")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value}
                      rows={4}
                      placeholder={t("descriptionPlaceholder")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="due_date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("dueDateHint")}>{t("dueDate")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} type="date" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="progress_pct"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("progressHint")}>{t("progress")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        type="number"
                        min={0}
                        max={100}
                        step={1}
                        inputMode="numeric"
                        onChange={(event) => field.onChange(event.target.value)}
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
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending
                  ? isEdit
                    ? t("saving")
                    : t("creating")
                  : isEdit
                    ? t("save")
                    : t("create")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
