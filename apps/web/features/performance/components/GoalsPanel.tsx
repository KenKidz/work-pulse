"use client";

/**
 * Goals tab — the caller's visible goals with status/employee filters,
 * pagination, the create/edit dialog and the activate/complete/cancel
 * transitions. Loading, empty, denied and error states are part of the
 * surface; a transition keeps the row unchanged on failure and renders the
 * mapped server sentence. Mutations invalidate the list, so every state flips
 * without a reload. Client leaf.
 */

import { CircleAlert, Plus, Target } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { formatPerformanceDate } from "../dates";
import { employeeLabel, goalStatusVariant, isForbiddenError } from "../labels";
import { useGoals, usePerformanceAccess, usePerformanceDirectory } from "../hooks/use-performance";
import type { Goal, GoalStatus, ListGoalsParams } from "../types";
import { GoalFormDialog } from "./GoalFormDialog";
import { GoalStateDialog, type GoalStateAction } from "./GoalStateDialog";
import { PerformanceDeniedState } from "./PerformanceDeniedState";
import { PerformancePagination } from "./PerformancePagination";

const ANY = "any";
const GOALS_PAGE_SIZE = 20;

const GOAL_STATUSES: GoalStatus[] = ["draft", "active", "completed", "cancelled"];

type OpenState = { goal: Goal; action: GoalStateAction };

export function GoalsPanel() {
  const t = useTranslations("performance.goals");
  const common = useTranslations("performance.actions");
  const status = useTranslations("performance.status.goal");
  const fallbacks = useTranslations("performance.fallbacks");
  const locale = useLocale();
  const { canManageGoals, canViewTeam } = usePerformanceAccess();
  const directory = usePerformanceDirectory();
  const [params, setParams] = useState<ListGoalsParams>({
    page: 1,
    page_size: GOALS_PAGE_SIZE,
  });
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Goal | null>(null);
  const [stateAction, setStateAction] = useState<OpenState | null>(null);

  const goalsQuery = useGoals(params);
  const goals = goalsQuery.data?.status === 200 ? goalsQuery.data.data.items : [];
  const pageInfo = goalsQuery.data?.status === 200 ? goalsQuery.data.data.page : null;
  const filtersActive = params.status !== undefined || params.employee_id !== undefined;
  // `PageParams` clamps a requested page up to 1 but not down to the last
  // page: a mutation that empties page 2 leaves it out of range.
  const pageOutOfRange = pageInfo !== null && pageInfo.page > Math.max(pageInfo.total_pages, 1);

  function change(next: Partial<ListGoalsParams>) {
    setParams((current) => ({ ...current, ...next }));
  }

  function openCreate() {
    // A new goal lands at the top of page 1, so return there before opening
    // the dialog (the recruitment/leave create pattern).
    setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(goal: Goal) {
    setEditing(goal);
    setFormOpen(true);
  }

  return (
    <section aria-labelledby="performance-goals-title" className="space-y-4">
      <div className="space-y-1">
        <h2 id="performance-goals-title" className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p className="max-w-2xl text-sm text-pretty text-muted-foreground">{t("description")}</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={params.status ?? ANY}
          onValueChange={(value) =>
            change({
              status: value === ANY ? undefined : (value as GoalStatus),
              page: 1,
            })
          }
        >
          <SelectTrigger aria-label={t("filters.statusAria")} className="w-full sm:w-40">
            <SelectValue placeholder={t("filters.anyStatus")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>{t("filters.anyStatus")}</SelectItem>
            {GOAL_STATUSES.map((goalStatus) => (
              <SelectItem key={goalStatus} value={goalStatus}>
                {status(goalStatus)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {canViewTeam ? (
          <Select
            value={params.employee_id ?? ANY}
            onValueChange={(value) =>
              change({
                employee_id: value === ANY ? undefined : value,
                page: 1,
              })
            }
          >
            <SelectTrigger aria-label={t("filters.employeeAria")} className="w-full sm:w-56">
              <SelectValue placeholder={t("filters.allEmployees")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>{t("filters.allEmployees")}</SelectItem>
              {directory.filterEmployees.map((employee) => (
                <SelectItem key={employee.id} value={employee.id}>
                  {`${employee.first_name} ${employee.last_name}`.trim()}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}

        {canManageGoals ? (
          <Button className="ml-auto" onClick={openCreate}>
            <Plus />
            {common("newGoal")}
          </Button>
        ) : null}
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {goalsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("loading")}</p>
          </div>
        ) : goalsQuery.isError ? (
          isForbiddenError(goalsQuery.error) ? (
            <div className="p-4">
              <PerformanceDeniedState message={t("denied")} />
            </div>
          ) : (
            <div className="p-4">
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>
                  <p>{t("loadError")}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void goalsQuery.refetch()}
                  >
                    {common("retry")}
                  </Button>
                </AlertDescription>
              </Alert>
            </div>
          )
        ) : goals.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={Target} message={filtersActive ? t("emptyFiltered") : t("empty")} />
            {pageOutOfRange ? (
              <div className="mt-3 flex justify-center">
                <Button variant="outline" size="sm" onClick={() => change({ page: 1 })}>
                  {common("backToFirstPage")}
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">{t("swipe")}</p>
            <Table aria-label={t("label")} containerLabel={t("containerLabel")}>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("columns.title")}</TableHead>
                  {canViewTeam ? <TableHead>{t("columns.employee")}</TableHead> : null}
                  <TableHead>{t("columns.due")}</TableHead>
                  <TableHead className="text-right">{t("columns.progress")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  {canManageGoals ? (
                    <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                  ) : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {goals.map((goal) => {
                  const terminal = goal.status === "completed" || goal.status === "cancelled";
                  return (
                    <TableRow key={goal.id}>
                      <TableCell className="pl-3 font-medium">{goal.title}</TableCell>
                      {canViewTeam ? (
                        <TableCell>
                          {employeeLabel(
                            directory.nameById,
                            goal.employee_id,
                            fallbacks("employee", { id: goal.employee_id.slice(-4) }),
                          )}
                        </TableCell>
                      ) : null}
                      <TableCell className="text-sm text-muted-foreground">
                        {goal.due_date === null || goal.due_date === undefined ? (
                          "—"
                        ) : (
                          <time dateTime={goal.due_date}>
                            {formatPerformanceDate(goal.due_date, locale)}
                          </time>
                        )}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {goal.progress_pct}%
                      </TableCell>
                      <TableCell>
                        <Badge variant={goalStatusVariant[goal.status]} data-status={goal.status}>
                          {status(goal.status)}
                        </Badge>
                      </TableCell>
                      {canManageGoals ? (
                        <TableCell className="pr-3 text-right">
                          {terminal ? (
                            <span className="text-sm text-muted-foreground">—</span>
                          ) : (
                            <div className="flex justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={t("editAria", { title: goal.title })}
                                onClick={() => openEdit(goal)}
                              >
                                {common("edit")}
                              </Button>
                              {goal.status === "draft" ? (
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="min-h-11 sm:min-h-7"
                                  aria-label={t("activateAria", { title: goal.title })}
                                  onClick={() => setStateAction({ goal, action: "activate" })}
                                >
                                  {common("activate")}
                                </Button>
                              ) : (
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="min-h-11 sm:min-h-7"
                                  aria-label={t("completeAria", { title: goal.title })}
                                  onClick={() => setStateAction({ goal, action: "complete" })}
                                >
                                  {common("complete")}
                                </Button>
                              )}
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={t("cancelAria", { title: goal.title })}
                                onClick={() => setStateAction({ goal, action: "cancel" })}
                              >
                                {common("cancel")}
                              </Button>
                            </div>
                          )}
                        </TableCell>
                      ) : null}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            {pageInfo === null ? null : (
              <PerformancePagination
                page={pageInfo}
                label={t("paginationLabel")}
                onPageChange={(page) => change({ page })}
              />
            )}
          </>
        )}
      </Card>

      {formOpen ? (
        // Mount per open: `useForm` keeps values across a hidden Radix Dialog,
        // so a persistent instance would reopen with stale values.
        <GoalFormDialog key={editing?.id ?? "new"} open onOpenChange={setFormOpen} goal={editing} />
      ) : null}
      {stateAction === null ? null : (
        <GoalStateDialog
          key={`${stateAction.goal.id}-${stateAction.action}`}
          open
          onOpenChange={(open) => {
            if (!open) setStateAction(null);
          }}
          goal={stateAction.goal}
          action={stateAction.action}
        />
      )}
    </section>
  );
}
