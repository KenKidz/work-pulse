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
import { CircleAlert, LogIn } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Textarea } from "@/components/ui/textarea";

import type { AttendanceRecord } from "../types";
import {
  attendanceFieldErrors,
  errorMessage,
  useCheckInAction,
  useCheckOutAction,
} from "../hooks/use-attendance";
import { workModeLabel } from "./labels";
import { checkInFormSchema, type CheckInFormValues } from "../schema";
import { formatClockTime } from "./dates";

type CheckInPanelProps = {
  /** The actor's own open record for the visible day, or `null` when none. */
  openRecord: AttendanceRecord | null;
};

export function CheckInPanel({ openRecord }: CheckInPanelProps) {
  const [rootError, setRootError] = useState<string | null>(null);
  const checkIn = useCheckInAction();
  const checkOut = useCheckOutAction();

  const form = useForm<CheckInFormValues>({
    resolver: zodResolver(checkInFormSchema),
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
      setRootError(attendanceFieldErrors(error).root ?? errorMessage(error));
    }
  }

  async function onCheckOut() {
    setRootError(null);
    try {
      await checkOut.mutateAsync({ id: openRecord?.id ?? "", data: { check_out_note: null } });
    } catch (error) {
      setRootError(attendanceFieldErrors(error).root ?? errorMessage(error));
    }
  }

  return (
    <Card data-testid="check-in-panel" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-3">
        <CardTitle>Your attendance</CardTitle>
        <CardDescription>Your check-in time is recorded automatically.</CardDescription>
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
                  You’re checked in {openRecord.work_mode === "remote" ? "remotely" : "on site"}
                  {openRecord.late ? " and late" : ""}.
                </p>
                <p className="text-xs text-muted-foreground">
                  Since{" "}
                  <time dateTime={openRecord.checked_in_at}>
                    {formatClockTime(openRecord.checked_in_at)}
                  </time>
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              onClick={() => void onCheckOut()}
              disabled={checkOut.isPending}
            >
              {checkOut.isPending ? "Checking out…" : "Check out"}
            </Button>
          </div>
        ) : (
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onCheckIn)}
              className="grid gap-4 sm:grid-cols-[12rem_minmax(0,1fr)_auto] sm:items-end"
              noValidate
            >
              <FormField
                control={form.control}
                name="work_mode"
                render={({ field }) => (
                  <FormItem className="gap-1.5">
                    <FormLabel>Work mode</FormLabel>
                    <FormControl>
                      <div
                        role="radiogroup"
                        aria-label="Work mode"
                        className="grid grid-cols-2 gap-1"
                      >
                        {(["onsite", "remote"] as const).map((mode) => (
                          <button
                            key={mode}
                            type="button"
                            role="radio"
                            aria-checked={field.value === mode}
                            data-testid={`work-mode-${mode}`}
                            onClick={() => field.onChange(mode)}
                            className={cn(
                              "min-h-11 cursor-pointer rounded-sm border px-3 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
                              field.value === mode
                                ? "border-primary bg-primary text-primary-foreground"
                                : "border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                            )}
                          >
                            {workModeLabel[mode]}
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
                    <FormLabel>Note (optional)</FormLabel>
                    <FormControl>
                      <Textarea
                        {...field}
                        value={field.value ?? ""}
                        rows={2}
                        className="min-h-11"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <Button type="submit" disabled={checkIn.isPending} className="min-h-11 sm:px-5">
                {checkIn.isPending ? "Checking in…" : "Check in"}
              </Button>
            </form>
          </Form>
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
