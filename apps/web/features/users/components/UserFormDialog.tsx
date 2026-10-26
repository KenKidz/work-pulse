"use client";

/**
 * Create/edit dialog for a user account — RHF + zod, server errors rendered
 * as visible field errors (AC12): `user_email_taken` → email,
 * `employee_link_taken`/`employee_not_found` → employee field, everything
 * else → root. Create pins the full `CreateUser` shape (email + password +
 * role + employee link); edit PATCHes email/role/link only (`active` is
 * never editable here — use the explicit activate/deactivate actions).
 * Employee options come from the employees feature's list hook; terminated
 * employees stay linkable (accounts and employment are separate lifecycles).
 * Client leaf.
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

import { useTranslations } from "next-intl";

import { useEmployees } from "@/features/employees/hooks/use-employees";

import {
  useUserCreate,
  useUserUpdate,
  userFieldErrors,
  type UserFieldName,
} from "../hooks/use-users";
import { userCreateSchema, userEditSchema, type UserCreateValues } from "../schema";
import { ALL_ROLES } from "./labels";

const NO_EMPLOYEE = "none";

type UserFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a user = edit (email/role/link only). */
  user: {
    id: string;
    email: string;
    role: UserCreateValues["role"];
    employee_id?: string | null;
  } | null;
};

export function UserFormDialog({ open, onOpenChange, user }: UserFormDialogProps) {
  const roles = useTranslations("auth.roles");
  const t = useTranslations("users");
  const common = useTranslations("common.actions");
  const errors = useTranslations("users.errors");
  const isEdit = user !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createUser = useUserCreate();
  const updateUser = useUserUpdate();
  const mutation = isEdit ? updateUser : createUser;

  // One form instance; the resolver switches to the edit schema so the
  // password field (not rendered there) cannot block the PATCH.
  const form = useForm<UserCreateValues>({
    resolver: zodResolver(
      isEdit
        ? userEditSchema((key) => t(`form.validation.${key}`))
        : userCreateSchema((key) => t(`form.validation.${key}`)),
    ) as unknown as Resolver<UserCreateValues>,
    defaultValues: {
      email: user?.email ?? "",
      password: "",
      role: user?.role ?? "employee",
      employee_id: user?.employee_id ?? null,
    },
  });

  async function onSubmit(values: UserCreateValues) {
    setRootError(null);

    try {
      if (user) {
        await updateUser.mutateAsync({
          id: user.id,
          data: {
            email: values.email,
            role: values.role,
            employee_id: values.employee_id ?? null,
          },
        });
      } else {
        await createUser.mutateAsync({
          data: {
            email: values.email,
            password: values.password,
            role: values.role,
            employee_id: values.employee_id ?? null,
          },
        });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = userFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as UserFieldName, {
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
          <DialogTitle>{isEdit ? t("form.editTitle") : t("form.createTitle")}</DialogTitle>
          <DialogDescription>
            {isEdit ? t("form.editDescription") : t("form.createDescription")}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("form.email")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="email"
                      placeholder={t("form.emailPlaceholder")}
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {!isEdit ? (
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("form.password")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="new-password" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="role"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("form.role")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("form.rolePlaceholder")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {ALL_ROLES.map((role) => (
                          <SelectItem key={role} value={role}>
                            {roles(role)}
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
                name="employee_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("form.employeeLink")}</FormLabel>
                    <EmployeeLinkPicker
                      value={field.value ?? null}
                      onChange={(value) => field.onChange(value)}
                    />
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

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
                {isEdit ? t("form.saveChanges") : t("form.createUser")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The employee-link select — a separate component so its list query mounts
 * with Radix's `DialogContent` (open only): a closed form costs no 100-row
 * fetch. Lists every employee — terminated employees stay linkable (pinned
 * edge case), so no status filter. The "No employee" sentinel submits an
 * explicit null (pinned PATCH unlink semantics).
 */
function EmployeeLinkPicker({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const t = useTranslations("users");
  const employeesQuery = useEmployees({ page_size: 100 });
  const employeeOptions = employeesQuery.data?.status === 200 ? employeesQuery.data.data.items : [];

  return (
    <Select
      value={value ?? NO_EMPLOYEE}
      onValueChange={(selected) => onChange(selected === NO_EMPLOYEE ? null : selected)}
    >
      <FormControl>
        <SelectTrigger>
          <SelectValue placeholder={t("form.noEmployee")} />
        </SelectTrigger>
      </FormControl>
      <SelectContent>
        <SelectItem value={NO_EMPLOYEE}>{t("form.noEmployee")}</SelectItem>
        {employeeOptions.map((option) => (
          <SelectItem key={option.id} value={option.id}>
            {option.first_name} {option.last_name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
