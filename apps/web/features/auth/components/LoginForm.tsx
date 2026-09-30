"use client";

/**
 * The login form — RHF + zod. `invalid_credentials` renders as a visible
 * error state and the user stays on the page (spec AC13); success navigates
 * to the dashboard. Already-authenticated visitors (stale cookie) redirect
 * straight to the dashboard. Password managers and paste are welcome:
 * standard inputs with `autocomplete` hints, nothing blocks either.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
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
import { z } from "zod";

import { authErrorMessage, useCurrentUser, useLogin } from "../hooks/use-auth";

const loginFormSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email"),
  password: z.string().min(1, "Password is required"),
});

type LoginFormValues = z.infer<typeof loginFormSchema>;

export function LoginForm() {
  const router = useRouter();
  const login = useLogin();
  const currentUser = useCurrentUser();
  const [formError, setFormError] = useState<string | null>(null);

  const resolved = currentUser.data?.status === 200;

  useEffect(() => {
    // A stale `wp_session` cookie on `/login`: the visitor is already in.
    if (resolved) {
      router.replace("/dashboard");
    }
  }, [resolved, router]);

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(loginFormSchema),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: LoginFormValues) {
    setFormError(null);
    try {
      await login.mutateAsync({ data: values });
      router.push("/dashboard");
    } catch (error) {
      // The user stays on the page; the error renders below (AC13).
      setFormError(authErrorMessage(error));
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
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type="email"
                  placeholder="you@workpulse.test"
                  autoComplete="email"
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
              <FormLabel>Password</FormLabel>
              <FormControl>
                <Input {...field} type="password" autoComplete="current-password" />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>
              <p>{formError}</p>
            </AlertDescription>
          </Alert>
        ) : null}

        <Button type="submit" className="w-full" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </Form>
  );
}
