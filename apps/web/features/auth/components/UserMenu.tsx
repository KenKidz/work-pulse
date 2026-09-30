"use client";

/**
 * Signed-in identity in the dashboard header: an initials avatar that opens
 * the account menu — who you are, and how to leave. The trigger is the same
 * 44px ghost control as the theme toggle and the mobile hamburger, so the
 * right cluster stays optically uniform at every breakpoint. Logout deletes
 * the session row and clears the cookie; the hook then clears the query cache
 * and routes to `/login`. Client leaf.
 */

import { LogOut } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { useAuth } from "./AuthGate";
import { initialsFromEmail, roleLabel } from "../labels";
import { useSignOut } from "../hooks/use-auth";

export function UserMenu() {
  const { user } = useAuth();
  const signOut = useSignOut();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-11"
          aria-label={`Account menu for ${user.email}`}
        >
          <Avatar>
            <AvatarFallback>{initialsFromEmail(user.email) || "?"}</AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="flex flex-col gap-1.5">
            <span
              className="truncate font-normal text-muted-foreground"
              title={user.email}
            >
              {user.email}
            </span>
            <Badge variant="secondary">{roleLabel[user.role]}</Badge>
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem
            variant="destructive"
            disabled={signOut.isPending}
            // `mutate`, not `mutateAsync`: a failed logout is reported by the
            // hook's onError toast and must not escape as an unhandled
            // rejection.
            onSelect={() => signOut.mutate()}
          >
            <LogOut aria-hidden="true" />
            {signOut.isPending ? "Signing out…" : "Sign out"}
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
