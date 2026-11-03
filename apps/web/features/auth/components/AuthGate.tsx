"use client";

/**
 * Client gate for the authenticated area (`app/(dashboard)`). Resolves the
 * session once via `useCurrentUser`: pending → quiet full-height spinner,
 * 401 → redirect to `/login`, resolved → render children with the user in
 * context. Shell navigation (`features/shell/`) reads the role from here so
 * admin-only destinations stay hidden for everyone else. Client leaf.
 */

import { logout } from "@workpulse/api-client";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { Spinner } from "@/components/ui/spinner";
import type { User } from "@workpulse/api-client/index.schemas";

import { useCurrentUser } from "../hooks/use-auth";

type AuthState = { user: User };

const AuthContext = createContext<AuthState | null>(null);

/** The resolved session user inside `(dashboard)`; throws outside the gate. */
export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used within AuthGate");
  }
  return context;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const t = useTranslations("auth.gate");
  const router = useRouter();
  const query = useCurrentUser();
  const user = query.data?.status === 200 ? query.data.data : null;

  const failed = query.isError;

  useEffect(() => {
    if (!failed) return;
    let active = true;
    // A missing/expired/revoked session — the cookie guard (proxy.ts) only
    // sees presence, so a stale cookie would otherwise loop /login →
    // /dashboard → 401 forever. The idempotent logout clears the cookie
    // server-side first; a network failure still lands on `/login`.
    void (async () => {
      try {
        await logout();
      } catch {
        // The API is unreachable — the redirect below still ends the loop.
      }
      if (active) {
        router.replace("/login");
        router.refresh();
      }
    })();
    return () => {
      active = false;
    };
  }, [failed, router]);

  if (query.isPending) {
    return (
      // The Spinner carries its own role="status"; the text explains it.
      <div className="flex min-h-dvh flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>{t("checkingSession")}</p>
      </div>
    );
  }

  if (user === null) {
    // Redirect in flight — render nothing so no protected chrome flashes.
    return null;
  }

  return <AuthContext.Provider value={{ user }}>{children}</AuthContext.Provider>;
}
