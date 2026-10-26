"use client";

/**
 * Admin password reset — a new password set without the current one; every
 * existing session of the user is revoked. The pinned 10-character minimum is
 * enforced client-side; the server stays the authority. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
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

import { useUserResetPassword, userFieldErrors } from "../hooks/use-users";
import { resetPasswordSchema, type ResetPasswordValues } from "../schema";
import type { User } from "../types";

type ResetPasswordDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The user being reset; `null` while the dialog is closed. */
  user: User | null;
};

export function ResetPasswordDialog({ open, onOpenChange, user }: ResetPasswordDialogProps) {
  const t = useTranslations("users");
  const common = useTranslations("common.actions");
  const errors = useTranslations("users.errors");
  const [rootError, setRootError] = useState<string | null>(null);
  const reset = useUserResetPassword();

  const form = useForm<ResetPasswordValues>({
    resolver: zodResolver(resetPasswordSchema((key) => t(`form.validation.${key}`))),
    defaultValues: { new_password: "" },
  });

  async function onSubmit(values: ResetPasswordValues) {
    if (user === null) return;
    setRootError(null);
    try {
      await reset.mutateAsync({ id: user.id, data: { new_password: values.new_password } });
      onOpenChange(false);
    } catch (error) {
      // Root-level, per the pinned mapping — same treatment as deactivate.
      setRootError(userFieldErrors(error, errors).root ?? t("errors.generic"));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setRootError(null);
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("reset.title")}</DialogTitle>
          <DialogDescription>
            {t("reset.description", { email: user?.email ?? "" })}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="new_password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("reset.newPassword")}</FormLabel>
                  <FormControl>
                    <Input {...field} type="password" autoComplete="new-password" />
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
              <Button type="submit" disabled={reset.isPending}>
                {t("reset.confirm")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
