"use client";

/**
 * The login form — RHF + zod. `invalid_credentials` renders as a visible
 * error state and the user stays on the page (spec AC13); success navigates
 * to the dashboard. Already-authenticated visitors (stale cookie) redirect
 * straight to the dashboard. Password managers and paste are welcome:
 * standard inputs with `autocomplete` hints, nothing blocks either, plus a
 * show/hide toggle (WCAG accessible-authentication guidance).
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert, Eye, EyeOff } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";

import { useApiErrorTranslator } from "@/lib/api-errors";
import { z } from "zod";

import { authErrorMessage, useCurrentUser, useLogin } from "../hooks/use-auth";

export function LoginForm() {
  const t = useTranslations("auth.login");
  const validation = useTranslations("auth.validation");
  const errors = useApiErrorTranslator("auth.errors");
  const router = useRouter();
  const login = useLogin();
  const currentUser = useCurrentUser();
  const [formError, setFormError] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  const loginFormSchema = z.object({
    email: z.string().min(1, validation("emailRequired")).email(validation("emailInvalid")),
    password: z.string().min(1, validation("passwordRequired")),
  });

  const resolved = currentUser.data?.status === 200;

  useEffect(() => {
    // A stale `wp_session` cookie on `/login`: the visitor is already in.
    if (resolved) {
      router.replace("/dashboard");
    }
  }, [resolved, router]);

  const form = useForm<z.infer<typeof loginFormSchema>>({
    resolver: zodResolver(loginFormSchema),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: z.infer<typeof loginFormSchema>) {
    setFormError(null);
    try {
      await login.mutateAsync({ data: values });
      router.push("/dashboard");
    } catch (error) {
      // The user stays on the page; the error renders below (AC13).
      setFormError(authErrorMessage(error, errors));
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("email")}</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type="email"
                  placeholder={t("emailPlaceholder")}
                  autoComplete="email"
                  autoFocus
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("password")}</FormLabel>
              <div className="relative">
                <FormControl>
                  <Input
                    {...field}
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    className="pr-12"
                  />
                </FormControl>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute top-1/2 right-1 size-11 -translate-y-1/2 sm:size-9"
                  aria-label={showPassword ? t("hidePassword") : t("showPassword")}
                  aria-pressed={showPassword}
                  onClick={() => setShowPassword((visible) => !visible)}
                >
                  {showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                </Button>
              </div>
              <FormMessage />
            </FormItem>
          )}
        />

        {formError ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>{formError}</p>
            </AlertDescription>
          </Alert>
        ) : null}

        <Button type="submit" className="min-h-11 w-full" disabled={login.isPending}>
          {login.isPending ? t("signingIn") : t("signIn")}
        </Button>
      </form>
    </Form>
  );
}
