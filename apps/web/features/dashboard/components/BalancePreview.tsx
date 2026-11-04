"use client";

/**
 * Two balance cells for the dashboard rails — annual and sick first, then
 * whatever else the employee has. Presentational only: the owning card fetches
 * balances and types, so the same shape renders on both dashboards.
 */

import { Wallet } from "lucide-react";
import { useTranslations } from "next-intl";

import { Spinner } from "@/components/ui/spinner";
import type { LeaveBalance, LeaveType } from "@/features/leave/types";

const BALANCES_TO_SHOW = 2;
const PREFERRED: Record<string, number> = { annual: 0, sick: 1 };

export function BalancePreview({
  balances,
  types,
  isPending,
  isError,
}: {
  balances: LeaveBalance[];
  types: LeaveType[];
  isPending: boolean;
  isError: boolean;
}) {
  const t = useTranslations("dashboard.myDay");
  const typeById = new Map(types.map((type) => [type.id, type]));
  const visible = [...balances]
    .sort(
      (a, b) =>
        (PREFERRED[typeById.get(a.leave_type_id)?.code ?? ""] ?? 9) -
        (PREFERRED[typeById.get(b.leave_type_id)?.code ?? ""] ?? 9),
    )
    .slice(0, BALANCES_TO_SHOW);

  if (isPending) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <span>{t("loading")}</span>
      </div>
    );
  }
  if (isError) {
    return <p className="text-sm text-muted-foreground">{t("balancesError")}</p>;
  }
  if (visible.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("noBalances")}</p>;
  }

  return (
    <dl className="grid grid-cols-2 gap-2">
      {visible.map((balance) => (
        <div
          key={`${balance.leave_type_id}-${balance.year}`}
          className="rounded-sm border border-border p-2.5"
        >
          <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Wallet className="size-3" aria-hidden="true" />
            {typeById.get(balance.leave_type_id)?.name ?? t("balanceFallback")}
          </dt>
          <dd className="mt-1 text-sm font-semibold tabular-nums">
            {t("balanceValue", {
              remaining: balance.remaining_days,
              entitlement: balance.entitlement_days,
            })}
          </dd>
        </div>
      ))}
    </dl>
  );
}
