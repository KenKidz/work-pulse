"use client";

/**
 * Header bell + unread badge + recent-notifications dropdown.
 *
 * Person-addressed by design (ADR 0015): the API only ever returns the caller's
 * own rows, so this component needs no role gate. The badge is pull-based —
 * `useUnreadCount` refetches on focus and every read-state mutation invalidates
 * the count, so the number settles without a reload (spec rule 8). A failed
 * count query degrades quietly: no badge, no toast, the menu still opens.
 *
 * Client leaf: mounted in the app-shell header, one per page.
 */

import { Bell, CheckCheck } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Spinner } from "@/components/ui/spinner";

import { formatNotificationTime } from "../dates";
import { useMarkAllReadAction, useNotifications, useUnreadCount } from "../hooks/use-notifications";
import { notificationKindLabel } from "../labels";
import type { NotificationItem } from "../types";

/** The dropdown is a glance, not the inbox: the most recent handful. */
export const BELL_PAGE_SIZE = 6;

function previewClassName(notification: NotificationItem): string {
  return notification.read_at === null ? "bg-muted/30" : "";
}

export function NotificationBell() {
  const unreadQuery = useUnreadCount();
  const recentQuery = useNotifications({ page: 1, page_size: BELL_PAGE_SIZE });
  const markAll = useMarkAllReadAction();

  const unreadCount = unreadQuery.data?.status === 200 ? unreadQuery.data.data.unread_count : 0;
  const showBadge = !unreadQuery.isError && unreadCount > 0;
  const badgeText = unreadCount > 99 ? "99+" : String(unreadCount);
  const label = unreadCount > 0 ? `Notifications, ${unreadCount} unread` : "Notifications";

  const items = recentQuery.data?.status === 200 ? recentQuery.data.data.items : [];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative size-11"
          aria-label={label}
          data-testid="notification-bell"
        >
          <Bell aria-hidden="true" />
          {showBadge ? (
            <span
              aria-hidden="true"
              data-testid="notification-unread-badge"
              className="absolute -top-0.5 -right-0.5 flex h-4.5 min-w-4.5 items-center justify-center rounded-full border-2 border-background bg-primary px-1 font-mono text-xs leading-none font-semibold text-primary-foreground tabular-nums"
            >
              {badgeText}
            </span>
          ) : null}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 sm:w-96">
        <DropdownMenuLabel className="flex items-center justify-between gap-2">
          <span>Notifications</span>
          {unreadCount > 0 ? <Badge variant="secondary">{unreadCount} unread</Badge> : null}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <div className="max-h-[min(60dvh,36rem)] overflow-y-auto">
          {recentQuery.isPending ? (
            <div className="flex items-center justify-center gap-2 px-3 py-6 text-sm text-muted-foreground">
              <Spinner className="size-4" />
              <p>Loading notifications…</p>
            </div>
          ) : recentQuery.isError ? (
            <p className="px-3 py-4 text-sm text-muted-foreground">Could not load notifications.</p>
          ) : items.length === 0 ? (
            <p className="px-3 py-4 text-sm text-muted-foreground">No notifications yet.</p>
          ) : (
            <ul className="divide-y">
              {items.map((notification) => (
                <li
                  key={notification.id}
                  data-testid="notification-preview"
                  data-read-state={notification.read_at ? "read" : "unread"}
                  className={previewClassName(notification)}
                >
                  <div className="flex items-start gap-2 px-3 py-2.5">
                    <span
                      aria-hidden="true"
                      className={
                        notification.read_at === null
                          ? "mt-1.5 size-2 shrink-0 rounded-full bg-primary"
                          : "mt-1.5 size-2 shrink-0"
                      }
                    />
                    <div className="min-w-0 space-y-1">
                      <p
                        className={
                          notification.read_at === null
                            ? "truncate text-sm font-semibold"
                            : "truncate text-sm"
                        }
                      >
                        {notification.subject}
                      </p>
                      <p className="line-clamp-2 text-xs text-muted-foreground">
                        {notification.body}
                      </p>
                      <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge variant="muted">{notificationKindLabel[notification.kind]}</Badge>
                        <time dateTime={notification.created_at} className="font-mono tabular-nums">
                          {formatNotificationTime(notification.created_at)}
                        </time>
                        {notification.read_at === null ? (
                          <span className="font-medium text-primary">Unread</span>
                        ) : null}
                      </p>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={unreadCount === 0 || markAll.isPending}
          // Keep the menu open while the mutation settles; the badge and the
          // preview rows update in place.
          onSelect={(event) => {
            event.preventDefault();
            markAll.mutate();
          }}
        >
          <CheckCheck aria-hidden="true" />
          {markAll.isPending ? "Marking all read…" : "Mark all read"}
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/notifications">View all notifications</Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
