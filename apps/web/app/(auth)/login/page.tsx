import type { Metadata } from "next";

import { LoginForm } from "@/features/auth/components/LoginForm";

export const metadata: Metadata = {
  title: "Sign in — WorkPulse",
};

export default function LoginPage() {
  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="text-sm text-muted-foreground">
          Accounts are provisioned by an administrator — there is no self-registration.
        </p>
      </div>
      <LoginForm />
    </section>
  );
}
