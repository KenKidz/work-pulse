"use client";

/**
 * Skills tab — the tenant catalog (search, archived toggle, create/rename,
 * archive/restore for HR/Admin) plus one employee's skill rows (attach from the
 * active catalog, proficiency 1–5, note, update/remove for a caller with
 * standing). Archived catalog rows are badged and never offered in the picker;
 * removing an employee row is final. Loading / empty / error / denied states
 * are part of both surfaces. Client leaf.
 */

import { CircleAlert, Plus, Search, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Input } from "@/components/ui/input";
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
import { useDebouncedValue } from "@/hooks/use-debounced-value";

import {
  useActiveSkills,
  useEmployeeSkills,
  usePerformanceAccess,
  usePerformanceDirectory,
  useSkills,
} from "../hooks/use-performance";
import { employeeLabel, isForbiddenError } from "../labels";
import type { EmployeeSkill, ListSkillsParams, Skill } from "../types";
import { EmployeeSkillFormDialog } from "./EmployeeSkillFormDialog";
import { EmployeeSkillRemoveDialog } from "./EmployeeSkillRemoveDialog";
import { PerformanceDeniedState } from "./PerformanceDeniedState";
import { PerformancePagination } from "./PerformancePagination";
import { SkillFormDialog } from "./SkillFormDialog";
import { SkillStateDialog, type SkillStateAction } from "./SkillStateDialog";

const SKILLS_PAGE_SIZE = 20;

type OpenState = { skill: Skill; action: SkillStateAction };

export function SkillsPanel() {
  const t = useTranslations("performance.skills");
  const status = useTranslations("performance.status.skill");
  const common = useTranslations("performance.actions");
  const { canManageCatalog } = usePerformanceAccess();
  const [params, setParams] = useState<ListSkillsParams>({
    page: 1,
    page_size: SKILLS_PAGE_SIZE,
  });
  // Typed search feeds the query through the 300 ms debounce; the page resets
  // immediately so the next request is page one.
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [stateAction, setStateAction] = useState<OpenState | null>(null);

  const skillsQuery = useSkills({
    ...params,
    q: debouncedSearch || undefined,
    include_archived: includeArchived,
  });
  const skills = skillsQuery.data?.status === 200 ? skillsQuery.data.data.items : [];
  const pageInfo = skillsQuery.data?.status === 200 ? skillsQuery.data.data.page : null;
  const filtersActive = searchInput.trim() !== "" || includeArchived;
  const pageOutOfRange = pageInfo !== null && pageInfo.page > Math.max(pageInfo.total_pages, 1);

  function change(next: Partial<ListSkillsParams>) {
    if ("q" in next) {
      setSearchInput(next.q ?? "");
      setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
      return;
    }
    setParams((current) => ({ ...current, ...next }));
  }

  function openCreate() {
    setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(skill: Skill) {
    setEditing(skill);
    setFormOpen(true);
  }

  return (
    <div className="space-y-8">
      <section aria-labelledby="performance-skills-title" className="space-y-4">
        <div className="space-y-1">
          <h2 id="performance-skills-title" className="text-lg font-semibold">
            {t("title")}
          </h2>
          <p className="max-w-2xl text-sm text-pretty text-muted-foreground">{t("description")}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-64">
            <Search className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label={t("filters.searchAria")}
              placeholder={t("filters.searchPlaceholder")}
              className="w-full pl-8"
              value={searchInput}
              onChange={(event) => change({ q: event.target.value || undefined })}
            />
          </div>

          <Button
            variant="outline"
            aria-pressed={includeArchived}
            onClick={() => {
              setIncludeArchived((value) => !value);
              setParams((current) => ({ ...current, page: 1 }));
            }}
          >
            {t("filters.showArchived")}
          </Button>

          {canManageCatalog ? (
            <Button className="ml-auto" onClick={openCreate}>
              <Plus />
              {common("newSkill")}
            </Button>
          ) : null}
        </div>

        <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
          {skillsQuery.isPending ? (
            <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
              <Spinner className="size-4" />
              <p>{t("loading")}</p>
            </div>
          ) : skillsQuery.isError ? (
            isForbiddenError(skillsQuery.error) ? (
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
                      onClick={() => void skillsQuery.refetch()}
                    >
                      {common("retry")}
                    </Button>
                  </AlertDescription>
                </Alert>
              </div>
            )
          ) : skills.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={Sparkles}
                message={filtersActive ? t("emptyFiltered") : t("empty")}
              />
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
                    <TableHead className="pl-3">{t("columns.name")}</TableHead>
                    <TableHead>{t("columns.description")}</TableHead>
                    <TableHead>{t("columns.status")}</TableHead>
                    {canManageCatalog ? (
                      <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                    ) : null}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {skills.map((skill) => {
                    const archived = skill.archived_at !== null && skill.archived_at !== undefined;
                    return (
                      <TableRow key={skill.id}>
                        <TableCell className="pl-3 font-medium">{skill.name}</TableCell>
                        <TableCell className="max-w-md text-sm text-muted-foreground">
                          {skill.description ?? "—"}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={archived ? "muted" : "secondary"}
                            data-status={archived ? "archived" : "active"}
                          >
                            {archived ? status("archived") : status("active")}
                          </Badge>
                        </TableCell>
                        {canManageCatalog ? (
                          <TableCell className="pr-3 text-right">
                            <div className="flex justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="xs"
                                className="min-h-11 sm:min-h-7"
                                aria-label={t("editAria", { name: skill.name })}
                                onClick={() => openEdit(skill)}
                              >
                                {common("edit")}
                              </Button>
                              {archived ? (
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="min-h-11 sm:min-h-7"
                                  aria-label={t("restoreAria", { name: skill.name })}
                                  onClick={() => setStateAction({ skill, action: "restore" })}
                                >
                                  {common("restore")}
                                </Button>
                              ) : (
                                <Button
                                  variant="ghost"
                                  size="xs"
                                  className="min-h-11 sm:min-h-7"
                                  aria-label={t("archiveAria", { name: skill.name })}
                                  onClick={() => setStateAction({ skill, action: "archive" })}
                                >
                                  {common("archive")}
                                </Button>
                              )}
                            </div>
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
      </section>

      <EmployeeSkillsSection />

      {formOpen ? (
        <SkillFormDialog
          key={editing?.id ?? "new"}
          open
          onOpenChange={setFormOpen}
          skill={editing}
        />
      ) : null}
      {stateAction === null ? null : (
        <SkillStateDialog
          key={`${stateAction.skill.id}-${stateAction.action}`}
          open
          onOpenChange={(open) => {
            if (!open) setStateAction(null);
          }}
          skill={stateAction.skill}
          action={stateAction.action}
        />
      )}
    </div>
  );
}

function EmployeeSkillsSection() {
  const t = useTranslations("performance.skills.employee");
  const common = useTranslations("performance.actions");
  const levels = useTranslations("performance.skills.levels");
  const fallbacks = useTranslations("performance.fallbacks");
  const { user, canViewTeam, canRecordSkills } = usePerformanceAccess();
  const directory = usePerformanceDirectory();
  const [subjectId, setSubjectId] = useState(user.employee_id ?? "");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<EmployeeSkill | null>(null);
  const [removing, setRemoving] = useState<EmployeeSkill | null>(null);

  // A tenant viewer without an employee link lands on the first active
  // employee (or the first record if nobody is active); anyone with a link
  // lands on themselves.
  const fallbackSubject =
    user.employee_id ??
    (canViewTeam
      ? (directory.pickerEmployees[0]?.id ?? directory.filterEmployees[0]?.id ?? "")
      : "");
  const effectiveSubjectId = subjectId !== "" ? subjectId : fallbackSubject;
  // History stays readable: terminated employees are selectable, even though
  // new goals/reviews are refused elsewhere.
  const subjectOptions = directory.filterEmployees;
  const employeeName = employeeLabel(
    directory.nameById,
    effectiveSubjectId,
    fallbacks("employee", { id: effectiveSubjectId.slice(-4) }),
  );

  const activeSkillsQuery = useActiveSkills();
  const activeSkills =
    activeSkillsQuery.data?.status === 200 ? activeSkillsQuery.data.data.items : [];
  const skillsQuery = useEmployeeSkills(effectiveSubjectId);
  const rows = skillsQuery.data?.status === 200 ? skillsQuery.data.data.items : [];

  return (
    <section aria-labelledby="performance-employee-skills-title" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <h2 id="performance-employee-skills-title" className="text-lg font-semibold">
            {t("title")}
          </h2>
          <p className="max-w-2xl text-sm text-pretty text-muted-foreground">{t("description")}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canViewTeam ? (
            <Select value={effectiveSubjectId} onValueChange={setSubjectId}>
              <SelectTrigger aria-label={t("subjectAria")} className="w-full sm:w-56">
                <SelectValue placeholder={t("selectEmployee")} />
              </SelectTrigger>
              <SelectContent>
                {subjectOptions.map((employee) => (
                  <SelectItem key={employee.id} value={employee.id}>
                    {`${employee.first_name} ${employee.last_name}`.trim()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}

          {canRecordSkills ? (
            <Button
              disabled={effectiveSubjectId === "" || activeSkills.length === 0}
              onClick={() => {
                setEditing(null);
                setFormOpen(true);
              }}
            >
              <Plus />
              {common("addSkill")}
            </Button>
          ) : null}
        </div>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {effectiveSubjectId === "" ? (
          <div className="p-4">
            <EmptyState icon={Sparkles} message={t("chooseEmployee")} />
          </div>
        ) : skillsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("loading")}</p>
          </div>
        ) : skillsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("loadError")}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void skillsQuery.refetch()}
                >
                  {common("retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : rows.length === 0 ? (
          <div className="p-4">
            <EmptyState icon={Sparkles} message={t("empty", { name: employeeName })} />
            {canRecordSkills && activeSkills.length === 0 ? (
              <p className="mt-3 text-center text-sm text-muted-foreground">{t("noCatalog")}</p>
            ) : null}
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">{t("swipe")}</p>
            <Table aria-label={t("label")} containerLabel={t("containerLabel")}>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("columns.skill")}</TableHead>
                  <TableHead>{t("columns.proficiency")}</TableHead>
                  <TableHead>{t("columns.note")}</TableHead>
                  {canRecordSkills ? (
                    <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                  ) : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.skill_id}>
                    <TableCell className="pl-3 font-medium">{row.skill_name}</TableCell>
                    <TableCell className="font-mono tabular-nums">
                      <span aria-label={t("proficiencyValue", { level: row.proficiency })}>
                        {levels("option", { level: row.proficiency })}
                      </span>
                    </TableCell>
                    <TableCell className="max-w-md text-sm text-muted-foreground">
                      {row.note ?? "—"}
                    </TableCell>
                    {canRecordSkills ? (
                      <TableCell className="pr-3 text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={t("editAria", { name: row.skill_name })}
                            onClick={() => {
                              setEditing(row);
                              setFormOpen(true);
                            }}
                          >
                            {common("edit")}
                          </Button>
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={t("removeAria", { name: row.skill_name })}
                            onClick={() => setRemoving(row)}
                          >
                            {common("remove")}
                          </Button>
                        </div>
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </Card>

      {formOpen ? (
        <EmployeeSkillFormDialog
          key={`${effectiveSubjectId}-${editing?.skill_id ?? "new"}`}
          open
          onOpenChange={setFormOpen}
          employeeId={effectiveSubjectId}
          employeeName={employeeName}
          skills={activeSkills}
          employeeSkill={editing}
        />
      ) : null}
      {removing === null ? null : (
        <EmployeeSkillRemoveDialog
          key={`${effectiveSubjectId}-${removing.skill_id}`}
          open
          onOpenChange={(open) => {
            if (!open) setRemoving(null);
          }}
          employeeId={effectiveSubjectId}
          employeeName={employeeName}
          employeeSkill={removing}
        />
      )}
    </section>
  );
}
