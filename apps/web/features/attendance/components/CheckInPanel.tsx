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
import { useState } from "react";
import { useForm } from "react-hook-form";

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
    <Card data-testid="check-in-panel">
      <CardHeader>
        <CardTitle>Your attendance</CardTitle>
        <CardDescription>
          Server time is authoritative — client timestamps are never accepted.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {openRecord ? (
          <div
            data-testid="open-record"
            className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/40 p-3 text-sm"
          >
            <div>
              <p className="font-medium">
                Checked in — {workModeLabel[openRecord.work_mode]}
                {openRecord.late ? " (late)" : ""}
              </p>
              <p className="text-muted-foreground">
                Since {formatClockTime(openRecord.checked_in_at)}
                {openRecord.workday ? ` · workday ${openRecord.workday}` : ""}
              </p>
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
            <form onSubmit={form.handleSubmit(onCheckIn)} className="space-y-4" noValidate>
              <FormField
                control={form.control}
                name="work_mode"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Work mode</FormLabel>
                    <FormControl>
                      <div role="radiogroup" aria-label="Work mode" className="flex w-fit gap-2">
                        {(["onsite", "remote"] as const).map((mode) => (
                          <button
                            key={mode}
                            type="button"
                            role="radio"
                            aria-checked={field.value === mode}
                            data-testid={`work-mode-${mode}`}
                            onClick={() => field.onChange(mode)}
                            className="rounded-md border px-3 py-1.5 text-sm aria-checked:border-primary aria-checked:bg-primary aria-checked:text-primary-foreground data-[active=true]:border-primary data-[active=true]:bg-primary data-[active=true]:text-primary-foreground"
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
                  <FormItem>
                    <FormLabel>Note (optional)</FormLabel>
                    <FormControl>
                      <Textarea {...field} value={field.value ?? ""} rows={2} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {rootError ? (
                <p role="alert" className="text-sm text-destructive">
                  {rootError}
                </p>
              ) : null}

              <Button type="submit" disabled={checkIn.isPending}>
                {checkIn.isPending ? "Checking in…" : "Check in"}
              </Button>
            </form>
          </Form>
        )}

        {openRecord ? (
          rootError ? (
            <p role="alert" className="text-sm text-destructive">
              {rootError}
            </p>
          ) : null
        ) : null}
      </CardContent>
    </Card>
  );
}
