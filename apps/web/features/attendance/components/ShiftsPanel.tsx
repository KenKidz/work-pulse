"use client";

/**
 * Shift management panel — the minimal reference data that makes lateness
 * real. Bare unpaged list (contract), create + edit dialogs, visible server
 * errors (`shift_scope_taken`, `shift_time_invalid`). Read is hr/admin-scoped
 * server-side; a denied role renders the document state. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { Pencil, Plus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import {
  attendanceFieldErrors,
  errorMessage,
  useShiftCreate,
  useShifts,
  useShiftUpdate,
} from "../hooks/use-attendance";
import type { Shift } from "../types";
import { shiftFormSchema, type ShiftFormValues } from "../schema";

export function ShiftsPanel() {
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
    <Card data-testid="shifts-panel">
      <CardHeader>
        <CardTitle>Shifts</CardTitle>
        <CardDescription>
          One shift per location scope plus a tenant default; location-scoped shifts win over the
          default when judging lateness.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <Button onClick={openCreate}>
            <Plus />
            New shift
          </Button>
        </div>

        {shiftsQuery.isPending ? (
          <p className="text-sm text-muted-foreground">Loading shifts…</p>
        ) : shiftsQuery.isError ? (
          <div role="alert" className="rounded-md border border-destructive/40 p-4 text-sm">
            {shiftsQuery.error instanceof ApiError &&
            (shiftsQuery.error.status === 403 || shiftsQuery.error.code === "forbidden") ? (
              <p>Only HR, admins and super admins can manage shifts.</p>
            ) : (
              <p>Could not load shifts.</p>
            )}
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => void shiftsQuery.refetch()}
            >
              Retry
            </Button>
          </div>
        ) : shifts.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No shifts configured — nobody is judged late until a default shift exists.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Window</TableHead>
                <TableHead>Grace</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shifts.map((shift) => (
                <TableRow key={shift.id}>
                  <TableCell className="font-medium">{shift.name}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {shift.start_time}–{shift.end_time}
                  </TableCell>
                  <TableCell>{shift.grace_minutes}m</TableCell>
                  <TableCell>
                    {shift.location_id ? `loc ${shift.location_id.slice(-4)}` : "Tenant default"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="xs"
                      aria-label={`Edit ${shift.name}`}
                      onClick={() => openEdit(shift)}
                    >
                      <Pencil />
                      Edit
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
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
  const isEdit = shift !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createShift = useShiftCreate();
  const updateShift = useShiftUpdate();
  const mutation = isEdit ? updateShift : createShift;

  const form = useForm<ShiftFormValues>({
    resolver: zodResolver(shiftFormSchema),
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
      setRootError(attendanceFieldErrors(error).root ?? errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit shift" : "New shift"}</DialogTitle>
          <DialogDescription>
            Times are `HH:MM` local to the scope. The lateness boundary is start + grace, exclusive.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder="Standard hours" autoComplete="off" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-2 gap-3">
              <FormField
                control={form.control}
                name="start_time"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Start time</FormLabel>
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
                    <FormLabel>End time</FormLabel>
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
                  <FormLabel>Grace minutes</FormLabel>
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
                Cancel
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {isEdit ? "Save changes" : "Create shift"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
