"use client";

/**
 * Shift management panel — the minimal reference data that makes lateness
 * real. Bare unpaged list (contract), create + edit dialogs, visible server
 * errors (`shift_scope_taken`, `shift_time_invalid`). Read is hr/admin-scoped
 * server-side; a denied role renders the document state. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert, Clock, Pencil, Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/empty-state";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { useApiErrorTranslator } from "@/lib/api-errors";

import {
  attendanceFieldErrors,
  errorMessage,
  isForbiddenError,
  useShiftCreate,
  useShifts,
  useShiftUpdate,
} from "../hooks/use-attendance";
import type { Shift } from "../types";
import { shiftFormSchema, type ShiftFormValues } from "../schema";

export function ShiftsPanel() {
  const t = useTranslations("attendance");
  const common = useTranslations("common.actions");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Shift | null>(null);

  const shiftsQuery = useShifts();
  const listResponse = shiftsQuery.data;
  const shifts: Shift[] = listResponse && listResponse.status === 200 ? listResponse.data : [];

  function openCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function openEdit(shift: Shift) {
    setEditing(shift);
    setDialogOpen(true);
  }

  return (
    <Card data-testid="shifts-panel" className="rounded-md border-border shadow-none">
      <CardHeader>
        <CardTitle>{t("shifts.title")}</CardTitle>
        <CardDescription>{t("shifts.description")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div>
          <Button onClick={openCreate} className="min-h-11">
            <Plus />
            {t("shifts.newShift")}
          </Button>
        </div>

        {shiftsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("shifts.loading")}</p>
          </div>
        ) : shiftsQuery.isError ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              {isForbiddenError(shiftsQuery.error) ? (
                <p>{t("shifts.forbidden")}</p>
              ) : (
                <p>{t("shifts.loadError")}</p>
              )}
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void shiftsQuery.refetch()}
              >
                {common("retry")}
              </Button>
            </AlertDescription>
          </Alert>
        ) : shifts.length === 0 ? (
          <EmptyState icon={Clock} message={t("shifts.empty")} />
        ) : (
          <>
            <p className="text-xs text-muted-foreground sm:hidden">{t("shifts.swipe")}</p>
            <Table aria-label={t("shifts.label")} containerLabel={t("shifts.containerLabel")}>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("shifts.columns.name")}</TableHead>
                  <TableHead>{t("shifts.columns.hours")}</TableHead>
                  <TableHead>{t("shifts.columns.grace")}</TableHead>
                  <TableHead>{t("shifts.columns.appliesTo")}</TableHead>
                  <TableHead className="text-right">{t("shifts.columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shifts.map((shift) => (
                  <TableRow key={shift.id}>
                    <TableCell className="font-medium">{shift.name}</TableCell>
                    <TableCell className="text-sm tabular-nums">
                      {shift.start_time}–{shift.end_time}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {t("shifts.graceMinutes", { minutes: shift.grace_minutes })}
                    </TableCell>
                    <TableCell>
                      {shift.location_id ? (
                        <span className="text-sm text-muted-foreground">
                          {t("shifts.locationShort", { id: shift.location_id.slice(-4) })}
                        </span>
                      ) : (
                        <Badge variant="secondary">{t("shifts.defaultAll")}</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="xs"
                        className="min-h-11 sm:min-h-7"
                        aria-label={t("shifts.editAria", { name: shift.name })}
                        onClick={() => openEdit(shift)}
                      >
                        <Pencil />
                        {common("edit")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}

        <ShiftFormDialog
          key={editing?.id ?? "new"}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          shift={editing}
        />
      </CardContent>
    </Card>
  );
}
type ShiftFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a shift = edit mode. */
  shift: Shift | null;
};

export function ShiftFormDialog({ open, onOpenChange, shift }: ShiftFormDialogProps) {
  const t = useTranslations("attendance");
  const common = useTranslations("common.actions");
  const errors = useApiErrorTranslator("attendance.errors");
  const isEdit = shift !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createShift = useShiftCreate();
  const updateShift = useShiftUpdate();
  const mutation = isEdit ? updateShift : createShift;

  const form = useForm<ShiftFormValues>({
    resolver: zodResolver(shiftFormSchema((key) => t(`validation.${key}`))),
    defaultValues: {
      name: shift?.name ?? "",
      start_time: shift?.start_time ?? "09:00",
      end_time: shift?.end_time ?? "18:00",
      grace_minutes: shift?.grace_minutes ?? 10,
    },
  });

  async function onSubmit(values: ShiftFormValues) {
    setRootError(null);
    try {
      if (isEdit) {
        await updateShift.mutateAsync({
          id: shift?.id ?? "",
          data: {
            name: values.name,
            start_time: values.start_time,
            end_time: values.end_time,
            grace_minutes: values.grace_minutes,
          },
        });
      } else {
        await createShift.mutateAsync({
          data: {
            name: values.name,
            start_time: values.start_time,
            end_time: values.end_time,
            grace_minutes: values.grace_minutes,
          },
        });
      }
      onOpenChange(false);
    } catch (error) {
      setRootError(attendanceFieldErrors(error, errors).root ?? errorMessage(error, errors));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? t("shifts.form.editTitle") : t("shifts.form.createTitle")}
          </DialogTitle>
          <DialogDescription>{t("shifts.form.description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("shifts.form.name")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder={t("shifts.form.namePlaceholder")}
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="start_time"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("shifts.form.startTime")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="time" step={60} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="end_time"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("shifts.form.endTime")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="time" step={60} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="grace_minutes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("shifts.form.graceMinutes")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="number"
                      min={0}
                      step={1}
                      value={field.value}
                      onChange={(event) =>
                        field.onChange(event.target.value === "" ? undefined : event.target.value)
                      }
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {rootError ? (
              <p role="alert" className="text-sm text-destructive">
                {rootError}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {isEdit ? t("shifts.form.saveChanges") : t("shifts.form.createShift")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
