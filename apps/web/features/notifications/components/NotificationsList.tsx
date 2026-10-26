"use client";

/**
 * The `/notifications` inbox: a paged, newest-first list of the caller's own
 * delivered messages with read-state actions.
 *
 * The rows are the stored message of record (subject/body rendered at consume
 * time) — never re-rendered here, and no delivery status is shown (spec rules
 * 5–6). Read state is the only thing this surface mutates; both actions
 * invalidate the list and the header badge through the shared hooks.
 */

import { BellOff, CheckCheck, CircleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";

import { formatNotificationTime } from "../dates";
import {
  useMarkAllReadAction,
  useMarkReadAction,
  useNotifications,
  useUnreadCount,
} from "../hooks/use-notifications";
import type { NotificationItem } from "../types";

export const NOTIFICATIONS_PAGE_SIZE = 20;

function rowClassName(notification: NotificationItem): string {
  return notification.read_at === null
    ? "flex gap-3 bg-muted/30 px-4 py-3"
    : "flex gap-3 px-4 py-3";
}

export function NotificationsList() {
  const t = useTranslations("notifications");
  const locale = useLocale();
  const common = useTranslations("common.actions");
  const [page, setPage] = useState(1);
  const notificationsQuery = useNotifications({
    page,
    page_size: NOTIFICATIONS_PAGE_SIZE,
  });
  const unreadQuery = useUnreadCount();
  const markRead = useMarkReadAction();
  const markAll = useMarkAllReadAction();

  const response = notificationsQuery.data;
  const notifications = response?.status === 200 ? response.data.items : [];
  const pageInfo = response?.status === 200 ? response.data.page : null;
  const unreadCount = unreadQuery.data?.status === 200 ? unreadQuery.data.data.unread_count : 0;

  return (
    <section aria-label={t("list.ariaLabel")} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {unreadCount > 0
            ? t("list.unreadCount", { count: unreadCount })
            : t("list.allCaughtUp")}
        </p>
        <Button
          variant="outline"
          className="min-h-11"
          disabled={unreadCount === 0 || markAll.isPending}
          onClick={() => markAll.mutate()}
        >
          <CheckCheck />
          {markAll.isPending ? t("list.markingAll") : t("list.markAll")}
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {notificationsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("list.loading")}</p>
          </div>
        ) : notificationsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("list.loadError")}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void notificationsQuery.refetch()}
                >
                  {common("retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : notifications.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={BellOff}
              message={t("list.empty")}
            />
          </div>
        ) : (
          <>
            <ul className="divide-y">
              {notifications.map((notification) => {
                const unread = notification.read_at === null;
                return (
                  <li
                    key={notification.id}
                    data-testid="notification-row"
                    data-read-state={unread ? "unread" : "read"}
                    className={rowClassName(notification)}
                  >
                    <span
                      aria-hidden="true"
                      className={
                        unread
                          ? "mt-1.5 size-2 shrink-0 rounded-full bg-primary"
                          : "mt-1.5 size-2 shrink-0"
                      }
                    />
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1 space-y-1">
                          <p className={unread ? "text-sm font-semibold" : "text-sm font-medium"}>
                            {notification.subject}
                          </p>
                          <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <Badge variant="muted">
                              {t(`kinds.${notification.kind}` as never)}
                            </Badge>
                            <span
                              className={
                                unread ? "font-medium text-primary" : "text-muted-foreground"
                              }
                            >
                              {unread ? t("list.unread") : t("list.read")}
                            </span>
                            <time
                              dateTime={notification.created_at}
                              className="font-mono tabular-nums"
                            >
                              {formatNotificationTime(notification.created_at, locale)}
                            </time>
                          </p>
                        </div>
                        {unread ? (
                          <Button
                            variant="outline"
                            size="sm"
                            className="min-h-11 shrink-0 sm:min-h-7"
                            aria-label={t("list.markReadAria", { subject: notification.subject })}
                            disabled={markRead.isPending}
                            onClick={() => markRead.mutate({ id: notification.id })}
                          >
                            {t("list.markRead")}
                          </Button>
                        ) : null}
                      </div>
                      <p className="max-w-2xl text-sm text-pretty text-muted-foreground">
                        {notification.body}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>

            {pageInfo && pageInfo.total_pages > 1 ? (
              <div className="flex items-center justify-end gap-2 border-t px-4 py-3 text-sm">
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={t("list.previousAria")}
                  disabled={pageInfo.page <= 1}
                  onClick={() => setPage(pageInfo.page - 1)}
                >
                  {t("list.previous")}
                </Button>
                <span className="text-muted-foreground">
                  {t("list.pageOf", { page: pageInfo.page, total: pageInfo.total_pages })}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={t("list.nextAria")}
                  disabled={pageInfo.page >= pageInfo.total_pages}
                  onClick={() => setPage(pageInfo.page + 1)}
                >
                  {t("list.next")}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Card>
    </section>
  );
}
