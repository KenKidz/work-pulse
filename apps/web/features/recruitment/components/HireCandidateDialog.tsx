"use client";

/**
 * Hire dialog — the last pipeline move. Terms plus optional contact overrides;
 * department/position/location always come from the job. The API creates the
 * Employee through the Employees seam in the same transaction, so a seam
 * failure (email/code taken, invalid manager) leaves the candidate `offered`
 * and renders in place. Client leaf.
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

import { useApiErrorTranslator } from "@/lib/api-errors";

import { todayISO } from "../dates";
import { recruitmentErrorMessage } from "../labels";
import {
  hireFieldErrors,
  useActiveEmployees,
  useCandidateHire,
  type HireFieldName,
} from "../hooks/use-recruitment";
import { hireFormSchema, type HireFormValues } from "../schema";
import type { Candidate, HireCandidate } from "../types";

const NO_MANAGER = "none";

type HireCandidateDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidate: Candidate;
  jobTitle: string;
};

export function HireCandidateDialog({
  open,
  onOpenChange,
  candidate,
  jobTitle,
}: HireCandidateDialogProps) {
  const t = useTranslations("recruitment.hire");
  const common = useTranslations("recruitment.actions");
  const validation = useTranslations("recruitment.validation");
  const errors = useApiErrorTranslator("recruitment.errors");
  const types = useTranslations("recruitment.types");
  const hireCandidate = useCandidateHire();
  const managers = useActiveEmployees();
  const [rootError, setRootError] = useState<string | null>(null);

  const form = useForm<HireFormValues>({
    resolver: zodResolver(hireFormSchema((key) => validation(key))),
    defaultValues: {
      employment_type: "full_time",
      hired_at: todayISO(),
      manager_id: NO_MANAGER,
      employee_code: "",
      first_name: "",
      last_name: "",
      email: "",
      phone: "",
    },
  });

  const name = `${candidate.first_name} ${candidate.last_name}`;

  async function onSubmit(values: HireFormValues) {
    setRootError(null);
    const data: HireCandidate = {
      employment_type: values.employment_type,
      hired_at: values.hired_at,
      manager_id: values.manager_id === NO_MANAGER ? undefined : values.manager_id,
      employee_code: values.employee_code === "" ? undefined : values.employee_code,
      first_name: values.first_name === "" ? undefined : values.first_name,
      last_name: values.last_name === "" ? undefined : values.last_name,
      email: values.email === "" ? undefined : values.email,
      phone: values.phone === "" ? undefined : values.phone,
    };

    try {
      await hireCandidate.mutateAsync({ candidateId: candidate.id, data });
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = hireFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? recruitmentErrorMessage(error, errors));
        } else {
          form.setError(field as Exclude<HireFieldName, "root">, {
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
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description", { name, job: jobTitle })}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="employment_type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("employmentType")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("selectType")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {(["full_time", "part_time", "contract", "intern"] as const).map(
                          (value) => (
                            <SelectItem key={value} value={value}>
                              {types(value)}
                            </SelectItem>
                          ),
                        )}
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
                    <FormLabel hint={t("hireDateHint")}>{t("hireDate")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} type="date" max={todayISO()} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="manager_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("manager")}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("noManager")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value={NO_MANAGER}>{t("noManager")}</SelectItem>
                        {managers.map((manager) => (
                          <SelectItem key={manager.id} value={manager.id}>
                            {manager.first_name} {manager.last_name}
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
                name="employee_code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("employeeCodeHint")}>{t("employeeCode")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} className="font-mono" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="space-y-2 border-t pt-4">
              <p className="text-sm font-medium">Contact details</p>
              <p className="text-xs text-muted-foreground">{t("overrideNote")}</p>
            </div>

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="first_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("firstName")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} placeholder={candidate.first_name} />
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
                    <FormLabel>{t("lastName")}</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} placeholder={candidate.last_name} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("email")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value}
                        type="email"
                        autoComplete="off"
                        placeholder={candidate.email}
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
                    <FormLabel>{t("phone")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value}
                        type="tel"
                        autoComplete="off"
                        placeholder={candidate.phone ?? t("noPhone")}
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
              <Button type="submit" disabled={hireCandidate.isPending}>
                {hireCandidate.isPending ? t("hiring") : t("hireAction")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
