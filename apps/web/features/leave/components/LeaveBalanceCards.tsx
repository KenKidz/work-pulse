"use client";

/**
 * The actor's own leave balances — one cell per `(leave type, year)`.
 * Balances are lazily created by the first request for that year, so an
 * empty card invites the first request instead of pretending a default.
 * Client leaf.
 */

import { CircleAlert, Wallet } from "lucide-react";
import { useMemo } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { useAuth } from "@/features/auth/components/AuthGate";

import { useLeaveBalances, useLeaveTypes } from "../hooks/use-leave";
import type { LeaveBalance } from "../types";

export function LeaveBalanceCards() {
  const { user } = useAuth();
  const employeeId = user.employee_id ?? null;
  const balancesQuery = useLeaveBalances(
    employeeId === null ? undefined : { employee_id: employeeId },
    employeeId !== null,
  );
  const typesQuery = useLeaveTypes();

  const balances = useMemo(
    () => (balancesQuery.data?.status === 200 ? balancesQuery.data.data : []),
    [balancesQuery.data],
  );
  const types = useMemo(
    () => (typesQuery.data?.status === 200 ? typesQuery.data.data : []),
    [typesQuery.data],
  );

  const nameById = useMemo(() => new Map(types.map((type) => [type.id, type.name])), [types]);

  const rows = useMemo(() => {
    const typeName = (balance: LeaveBalance) => nameById.get(balance.leave_type_id) ?? "Leave";
    return [...balances].sort((a, b) => b.year - a.year || typeName(a).localeCompare(typeName(b)));
  }, [balances, nameById]);

  // Accounts without an employee link cannot request leave, so a personal
  // balance section would be noise (HR/admin inboxes still render below).
  if (employeeId === null) return null;

  return (
    <section aria-labelledby="leave-balances-title" className="flex flex-col gap-4">
      <div className="space-y-1">
        <h2 id="leave-balances-title" className="text-lg leading-6 font-semibold tracking-tight">
          Your balances
        </h2>
        <p className="max-w-2xl text-sm leading-5 text-pretty text-muted-foreground">
          Remaining days per leave type and year. Approval deducts from the year of the start date.
        </p>
      </div>

      {balancesQuery.isPending ? (
        <div className="flex items-center justify-center gap-2 rounded-md border border-dashed px-6 py-10 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          <p>Loading your balances…</p>
        </div>
      ) : balancesQuery.isError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>
            <p>Could not load your leave balances.</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => void balancesQuery.refetch()}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : rows.length === 0 ? (
        <Card className="gap-0 rounded-md border-border py-0 shadow-none">
          <div className="p-4">
            <EmptyState
              icon={Wallet}
              message="No balances yet — annual and sick balances appear with your first request for that year."
            />
          </div>
        </Card>
      ) : (
        <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
          <dl className="grid grid-cols-1 gap-px bg-border sm:grid-cols-2 lg:grid-cols-3">
            {rows.map((balance) => (
              <div key={`${balance.leave_type_id}-${balance.year}`} className="bg-card p-4 sm:p-5">
                <dt className="text-sm font-medium text-muted-foreground">
                  {nameById.get(balance.leave_type_id) ?? "Leave"} ·{" "}
                  <span className="tabular-nums">{balance.year}</span>
                </dt>
                <dd className="mt-1.5 text-3xl leading-none font-semibold tabular-nums">
                  {balance.remaining_days}
                  <span className="ml-1 text-sm font-normal text-muted-foreground">days left</span>
                </dd>
                <p className="mt-2 text-xs text-muted-foreground tabular-nums">
                  {balance.used_days} used of {balance.entitlement_days} days
                </p>
              </div>
            ))}
          </dl>
        </Card>
      )}
    </section>
  );
}
