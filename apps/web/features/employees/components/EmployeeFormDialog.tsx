"use client";

/**
 * Create/edit dialog for a single employee — RHF + zod, server errors
 * rendered as visible field errors (AC12). Create mode pins the full
 * `CreateEmployee` shape: profile + assignment + employment type + hire date.
 * Edit mode patches profile fields only (pinned PATCH scope — assignment
 * changes go through transfers). Non-archived organization data feeds the
 * pickers. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm, type Resolver } from "react-hook-form";

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

import { useDepartments } from "@/features/organizations/hooks/use-departments";
import { usePositions } from "@/features/organizations/hooks/use-positions";
import { useLocations } from "@/features/organizations/hooks/use-locations";

import {
  employeeFieldErrors,
  useEmployees,
  useEmployeeCreate,
  useEmployeeUpdate,
  type EmployeeFieldName,
} from "../hooks/use-employees";
import { employeeCreateSchema, employeeProfileSchema, type EmployeeCreateValues } from "../schema";

const NO_MANAGER = "none";

type EmployeeFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; an employee = edit (profile fields only). */
  employee: {
    id: string;
    first_name: string;
    last_name: string;
    email: string;
    phone?: string | null;
  } | null;
};

export function EmployeeFormDialog({ open, onOpenChange, employee }: EmployeeFormDialogProps) {
  const isEdit = employee !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createEmployee = useEmployeeCreate();
  const updateEmployee = useEmployeeUpdate();
  const mutation = isEdit ? updateEmployee : createEmployee;

  // Pickers show only non-archived organization data (pinned contract).
  const departmentsQuery = useDepartments({ include_archived: false, page_size: 100 });
  const positionsQuery = usePositions({ include_archived: false, page_size: 100 });
  const locationsQuery = useLocations({ include_archived: false, page_size: 100 });
  const departmentOptions =
    departmentsQuery.data?.status === 200 ? departmentsQuery.data.data.items : [];
  const positionOptions = positionsQuery.data?.status === 200 ? positionsQuery.data.data.items : [];
  const locationOptions = locationsQuery.data?.status === 200 ? locationsQuery.data.data.items : [];
  const managerOptions = useEmployees({ status: "active", page_size: 100 });
  const managerChoices =
    managerOptions.data?.status === 200
      ? managerOptions.data.data.items.filter((option) => option.id !== employee?.id)
      : [];

  // One form instance; the resolver switches to the profile-only schema in
  // edit mode so assignment fields (not rendered there) cannot block the
  // pinned PATCH of name/email/phone.
  const form = useForm<EmployeeCreateValues>({
    resolver: zodResolver(
      isEdit ? employeeProfileSchema : employeeCreateSchema,
    ) as unknown as Resolver<EmployeeCreateValues>,
    defaultValues: {
      first_name: employee?.first_name ?? "",
      last_name: employee?.last_name ?? "",
      email: employee?.email ?? "",
      phone: employee?.phone ?? "",
      department_id: "",
      position_id: "",
      location_id: "",
      manager_id: null,
      employment_type: "full_time",
      hired_at: "",
    },
  });

  async function onSubmit(values: EmployeeCreateValues) {
    setRootError(null);

    try {
      if (employee) {
        await updateEmployee.mutateAsync({
          id: employee.id,
          data: {
            first_name: values.first_name,
            last_name: values.last_name,
            email: values.email,
            phone: values.phone || null,
          },
        });
      } else {
        await createEmployee.mutateAsync({
          data: {
            first_name: values.first_name,
            last_name: values.last_name,
            email: values.email,
            phone: values.phone || null,
            department_id: values.department_id,
            position_id: values.position_id,
            location_id: values.location_id,
            manager_id: values.manager_id ?? null,
            employment_type: values.employment_type,
            hired_at: values.hired_at,
          },
        });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = employeeFieldErrors(error);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as EmployeeFieldName, {
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
          <DialogTitle>{isEdit ? "Edit employee" : "New employee"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Corrections apply to profile fields; assignment changes go through transfers."
              : "Create the employee record and open their first employment period."}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="first_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>First name</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="Alex" autoComplete="off" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="last_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Last name</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="Nguyen" autoComplete="off" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="email"
                      placeholder="alex@example.com"
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="phone"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Phone (optional)</FormLabel>
                  <FormControl>
                    <Input {...field} value={field.value ?? ""} autoComplete="off" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {!isEdit ? (
              <>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
                  <FormField
                    control={form.control}
                    name="position_id"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Position</FormLabel>
                        <Select value={field.value} onValueChange={field.onChange}>
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select a position" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
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
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormField
                    control={form.control}
                    name="location_id"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Location</FormLabel>
                        <Select value={field.value} onValueChange={field.onChange}>
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select a location" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
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
                  <FormField
                    control={form.control}
                    name="manager_id"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Manager (optional)</FormLabel>
                        <Select
                          value={field.value ?? NO_MANAGER}
                          onValueChange={(value) =>
                            field.onChange(value === NO_MANAGER ? null : value)
                          }
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="No manager" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
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
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormField
                    control={form.control}
                    name="employment_type"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Employment type</FormLabel>
                        <Select value={field.value} onValueChange={field.onChange}>
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select a type" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            <SelectItem value="full_time">Full-time</SelectItem>
                            <SelectItem value="part_time">Part-time</SelectItem>
                            <SelectItem value="contract">Contract</SelectItem>
                            <SelectItem value="intern">Intern</SelectItem>
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="hired_at"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Hire date</FormLabel>
                        <FormControl>
                          <Input {...field} type="date" />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              </>
            ) : null}

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
                {isEdit ? "Save changes" : "Create employee"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
