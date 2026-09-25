"use client";

/**
 * Create/edit dialog for a single department — RHF + zod, server errors
 * rendered as visible field errors (AC9). Client leaf.
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

import {
  departmentFieldErrors,
  useDepartments,
  useDepartmentCreate,
  useDepartmentUpdate,
  type DepartmentFieldName,
} from "../hooks/use-departments";
import { departmentFormSchema, type DepartmentFormValues } from "../schema";
import type { Department } from "../types";

const NO_PARENT = "none";

type DepartmentFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a department = edit mode. */
  department: Department | null;
};

export function DepartmentFormDialog({
  open,
  onOpenChange,
  department,
}: DepartmentFormDialogProps) {
  const isEdit = department !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createDepartment = useDepartmentCreate();
  const updateDepartment = useDepartmentUpdate();
  const mutation = isEdit ? updateDepartment : createDepartment;

  // Archived departments are hidden from pickers (pinned contract).
  const parentsQuery = useDepartments({ include_archived: false, page_size: 100 });
  const parentsResponse = parentsQuery.data;
  const parentOptions = (
    parentsResponse && parentsResponse.status === 200 ? parentsResponse.data.items : []
  ).filter((option) => option.id !== department?.id);

  const form = useForm<DepartmentFormValues>({
    resolver: zodResolver(departmentFormSchema),
    defaultValues: {
      name: department?.name ?? "",
      parent_department_id: department?.parent_department_id ?? null,
    },
  });

  async function onSubmit(values: DepartmentFormValues) {
    setRootError(null);
    const payload = {
      name: values.name,
      parent_department_id: values.parent_department_id ?? null,
    };

    try {
      if (department) {
        await updateDepartment.mutateAsync({ id: department.id, data: payload });
      } else {
        await createDepartment.mutateAsync({ data: payload });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = departmentFieldErrors(error);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as DepartmentFieldName, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit department" : "New department"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Rename the department or move it in the hierarchy."
              : "Departments can be nested under a parent department."}
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
                    <Input {...field} placeholder="Engineering" autoComplete="off" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="parent_department_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Parent department</FormLabel>
                  <Select
                    value={field.value ?? NO_PARENT}
                    onValueChange={(value) => field.onChange(value === NO_PARENT ? null : value)}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a parent" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value={NO_PARENT}>No parent (root)</SelectItem>
                      {parentOptions.map((option) => (
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
                {isEdit ? "Save changes" : "Create department"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
