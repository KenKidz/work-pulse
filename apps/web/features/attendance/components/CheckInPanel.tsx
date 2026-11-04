"use client";

/**
 * Check-in / check-out panel — the actor's own action.
 *
 * Server time is authoritative (Decision #3): the form exposes only the
 * required `work_mode` toggle and an optional note. The open state renders as
 * an open record banner with a Check out action; a rejected check-in (e.g.
 * `409 attendance_already_checked_in`) renders as the pinned visible error
 * state (AC12) and stays until a refresh settles. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert, LogIn, LogOut } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { cn } from "cn";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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

import type { AttendanceRecord } from "../types";
import {
  attendanceFieldErrors,
  errorMessage,
  useCheckInAction,
  useCheckOutAction,
} from "../hooks/use-attendance";
import { checkInFormSchema, type CheckInFormValues } from "../schema";
import { formatClockTime, formatDate } from "./dates";

type CheckInPanelProps = {
  /** The actor's own open record for the visible day, or `null` when none. */
  openRecord: AttendanceRecord | null;
};

export function CheckInPanel({ openRecord }: CheckInPanelProps) {
  const t = useTranslations("attendance.checkIn");
  const locale = useLocale();
  const workModes = useTranslations("attendance.workModes");
  const validation = useTranslations("attendance.validation");
  const errors = useApiErrorTranslator("attendance.errors");
  const [rootError, setRootError] = useState<string | null>(null);
  const checkIn = useCheckInAction();
  const checkOut = useCheckOutAction();

  const form = useForm<CheckInFormValues>({
    resolver: zodResolver(checkInFormSchema((key) => validation(key))),
    defaultValues: {
      work_mode: "onsite",
      check_in_note: null,
    },
  });

  async function onCheckIn(values: CheckInFormValues) {
    setRootError(null);
    try {
      await checkIn.mutateAsync({
        data: {
          work_mode: values.work_mode,
          check_in_note: values.check_in_note?.trim() ? values.check_in_note : null,
        },
      });
      form.reset({ work_mode: "onsite", check_in_note: null });
    } catch (error) {
      setRootError(attendanceFieldErrors(error, errors).root ?? errorMessage(error, errors));
    }
  }

  async function onCheckOut() {
    setRootError(null);
    try {
      await checkOut.mutateAsync({ id: openRecord?.id ?? "", data: { check_out_note: null } });
    } catch (error) {
      setRootError(attendanceFieldErrors(error, errors).root ?? errorMessage(error, errors));
    }
  }

  return (
    <Card data-testid="check-in-panel" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-3">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
        <CardAction>
          <Badge variant={openRecord ? "success" : "muted"} data-testid="attendance-status">
            {openRecord ? t("statusCheckedIn") : t("statusNotCheckedIn")}
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 px-5 pb-5">
        {openRecord ? (
          <div
            data-testid="open-record"
            className="flex flex-wrap items-center justify-between gap-4 rounded-sm border border-success/30 bg-success/5 p-4 text-sm"
          >
            <div className="flex items-center gap-3">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-sm bg-success/10 text-success">
                <LogIn className="size-4" aria-hidden="true" />
              </span>
              <div>
                <p role="status" className="font-medium">
                  {t("checkedIn", {
                    mode: openRecord.work_mode === "remote" ? t("remotely") : t("onSite"),
                    late: openRecord.late ? t("lateSuffix") : "",
                  })}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("since")}{" "}
                  <time dateTime={openRecord.checked_in_at}>
                    {formatClockTime(openRecord.checked_in_at, locale)} ·{" "}
                    {formatDate(openRecord.workday, locale)}
                  </time>
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              className="min-h-11"
              onClick={() => void onCheckOut()}
              disabled={checkOut.isPending}
            >
              <LogOut aria-hidden="true" />
              {checkOut.isPending ? t("checkingOut") : t("checkOut")}
            </Button>
          </div>
        ) : (
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onCheckIn)}
              className="grid gap-4 sm:grid-cols-[14rem_minmax(0,1fr)_auto] sm:items-end"
              noValidate
            >
              <FormField
                control={form.control}
                name="work_mode"
                render={({ field }) => (
                  <FormItem className="gap-1.5">
                    <FormLabel>{t("workMode")}</FormLabel>
                    <FormControl>
                      <div
                        role="radiogroup"
                        aria-label={t("workModeAria")}
                        className="grid grid-cols-2"
                      >
                        {(["onsite", "remote"] as const).map((mode, index) => (
                          <button
                            key={mode}
                            type="button"
                            role="radio"
                            aria-checked={field.value === mode}
                            data-testid={`work-mode-${mode}`}
                            onClick={() => field.onChange(mode)}
                            className={cn(
                              "min-h-11 cursor-pointer border border-input px-3 text-sm font-medium transition-colors outline-none focus-visible:z-10 focus-visible:ring-[3px] focus-visible:ring-ring/50",
                              index === 0 ? "rounded-l-md" : "-ml-px rounded-r-md",
                              field.value === mode
                                ? "border-primary bg-primary text-primary-foreground shadow-xs"
                                : "bg-muted text-muted-foreground hover:bg-background/70 hover:text-foreground",
                            )}
                          >
                            {workModes(mode)}
                          </button>
                        ))}
                      </div>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="check_in_note"
                render={({ field }) => (
                  <FormItem className="gap-1.5">
                    <FormLabel>{t("note")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        value={field.value ?? ""}
                        placeholder={t("notePlaceholder")}
                        className="min-h-11"
                        // Clearing an optional note makes it absent, not a
                        // failed min-length validation.
                        onChange={(event) =>
                          field.onChange(event.target.value === "" ? null : event.target.value)
                        }
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <Button type="submit" disabled={checkIn.isPending} className="min-h-11 sm:px-5">
                <LogIn aria-hidden="true" />
                {checkIn.isPending ? t("checkingIn") : t("checkIn")}
              </Button>
            </form>
          </Form>
        )}

        {openRecord ? null : (
          <p className="text-xs leading-5 text-muted-foreground">{t("lateHint")}</p>
        )}

        {rootError ? (
          <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            {rootError}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
