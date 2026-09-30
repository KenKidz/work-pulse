"use client";

/**
 * Signed-in identity in the dashboard header: the account email plus the
 * sign-out action. Logout deletes the session row and clears the cookie;
 * the hook then clears the query cache and routes to `/login`. Client leaf.
 */

import { Button } from "@/components/ui/button";

import { useAuth } from "./AuthGate";
import { useSignOut } from "../hooks/use-auth";

export function UserMenu() {
  const { user } = useAuth();
  const signOut = useSignOut();

  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-sm text-muted-foreground sm:inline">{user.email}</span>
      <Button
        variant="ghost"
        size="sm"
        className="min-h-11 sm:min-h-9"
        onClick={() => void signOut.mutateAsync()}
        disabled={signOut.isPending}
      >
        {signOut.isPending ? "Signing out…" : "Sign out"}
      </Button>
    </div>
  );
}
