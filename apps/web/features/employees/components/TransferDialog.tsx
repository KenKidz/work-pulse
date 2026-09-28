"use client";

/**
 * Transfer dialog — closes the current employment period and appends a new
 * one at `effective_from` (append-only history, pinned semantics). Omitted
 * refs inherit the current period's values; department is required. Server
 * errors map to visible field errors (AC12). Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
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

import { useDepartments } from "@/features/organizations/hooks/use-departments";
import { usePositions } from "@/features/organizations/hooks/use-positions";
import { useLocations } from "@/features/organizations/hooks/use-locations";

import {
  employeeFieldErrors,
  useEmployees,
  useEmployeeTransfer,
  type TransferFieldName,
} from "../hooks/use-employees";
import {
  transferFormSchema,
  TRANSFER_KEEP,
  TRANSFER_NO_MANAGER,
  type TransferFormValues,
} from "../schema";
import { todayISO } from "./dates";

const KEEP = TRANSFER_KEEP;
const NO_MANAGER = TRANSFER_NO_MANAGER;

type TransferDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employeeId: string;
  currentDepartmentId: string;
};

export function TransferDialog({
  open,
  onOpenChange,
  employeeId,
  currentDepartmentId,
}: TransferDialogProps) {
  const [rootError, setRootError] = useState<string | null>(null);
  const transfer = useEmployeeTransfer();

  // Pickers show only non-archived organization data (pinned contract).
  const departmentsQuery = useDepartments({ include_archived: false, page_size: 100 });
  const positionsQuery = usePositions({ include_archived: false, page_size: 100 });
  const locationsQuery = useLocations({ include_archived: false, page_size: 100 });
  const departmentOptions =
    departmentsQuery.data?.status === 200 ? departmentsQuery.data.data.items : [];
  const positionOptions = positionsQuery.data?.status === 200 ? positionsQuery.data.data.items : [];
  const locationOptions = locationsQuery.data?.status === 200 ? locationsQuery.data.data.items : [];

  // Managers are active employees other than the one being transferred
  // (self-manager rule); a separate query avoids circular filter deps.
  const managersQuery = useEmployees({ status: "active", page_size: 100 });
  const managerChoices =
    managersQuery.data?.status === 200
      ? managersQuery.data.data.items.filter((option) => option.id !== employeeId)
      : [];

  const form = useForm<TransferFormValues>({
    resolver: zodResolver(transferFormSchema),
    defaultValues: {
      department_id: currentDepartmentId,
      position_id: KEEP,
      location_id: KEEP,
      manager_id: KEEP,
      effective_from: todayISO(),
      reason: "",
    },
  });

  async function onSubmit(values: TransferFormValues) {
    setRootError(null);
    try {
      // Pinned semantics: omitted fields inherit the current period; the API
      // rejects explicit null for position/location, so "keep" drops the key.
      // `undefined` properties are dropped by JSON.stringify.
      await transfer.mutateAsync({
        id: employeeId,
        data: {
          department_id: values.department_id,
          position_id: values.position_id === KEEP ? undefined : values.position_id,
          location_id: values.location_id === KEEP ? undefined : values.location_id,
          manager_id:
            values.manager_id === KEEP
              ? undefined
              : values.manager_id === NO_MANAGER
                ? null
                : values.manager_id,
          effective_from: values.effective_from,
          reason: values.reason || null,
        },
      });
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = employeeFieldErrors(error);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as TransferFieldName, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Transfer employee</DialogTitle>
          <DialogDescription>
            Closes the current employment period and opens a new one at the effective date. Fields
            left as &quot;keep&quot; inherit the current assignment.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="department_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Department</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a department" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {departmentOptions.map((option) => (
                        <SelectItem key={option.id} value={option.id}>
                          {option.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="position_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Position</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Keep current" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value={KEEP}>Keep current</SelectItem>
                        {positionOptions.map((option) => (
                          <SelectItem key={option.id} value={option.id}>
                            {option.title}
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
                name="location_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Location</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Keep current" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value={KEEP}>Keep current</SelectItem>
                        {locationOptions.map((option) => (
                          <SelectItem key={option.id} value={option.id}>
                            {option.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="manager_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Manager</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Keep current" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value={KEEP}>Keep current</SelectItem>
                      <SelectItem value={NO_MANAGER}>No manager</SelectItem>
                      {managerChoices.map((option) => (
                        <SelectItem key={option.id} value={option.id}>
                          {option.first_name} {option.last_name}
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
              name="effective_from"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Effective from</FormLabel>
                  <FormControl>
                    <Input {...field} type="date" max={todayISO()} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="reason"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Reason (optional)</FormLabel>
                  <FormControl>
                    <Textarea {...field} value={field.value ?? ""} rows={2} />
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
              <Button type="submit" disabled={transfer.isPending}>
                Record transfer
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
