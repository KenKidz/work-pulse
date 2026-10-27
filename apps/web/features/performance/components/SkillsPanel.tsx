"use client";

/**
 * Skills tab — the tenant skill catalog. T12 adds catalog management and the
 * per-employee rows; the catalog read surface is useful on its own and stays
 * the reference list every employee skill points at. Client leaf.
 */

import { CircleAlert, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { isForbiddenError } from "../labels";
import { useSkills } from "../hooks/use-performance";
import { PerformanceDeniedState } from "./PerformanceDeniedState";
import { PerformancePagination } from "./PerformancePagination";

const SKILLS_PAGE_SIZE = 20;

export function SkillsPanel() {
  const t = useTranslations("performance.skills");
  const status = useTranslations("performance.status.skill");
  const common = useTranslations("performance.actions");
  const [page, setPage] = useState(1);

  const skillsQuery = useSkills({ page, page_size: SKILLS_PAGE_SIZE });
  const skills = skillsQuery.data?.status === 200 ? skillsQuery.data.data.items : [];
  const pageInfo = skillsQuery.data?.status === 200 ? skillsQuery.data.data.page : null;
  const pageOutOfRange = pageInfo !== null && pageInfo.page > Math.max(pageInfo.total_pages, 1);

  return (
    <section aria-labelledby="performance-skills-title" className="space-y-4">
      <div className="space-y-1">
        <h2 id="performance-skills-title" className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p className="max-w-2xl text-sm text-pretty text-muted-foreground">{t("description")}</p>
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
            <EmptyState icon={Sparkles} message={t("empty")} />
            {pageOutOfRange ? (
              <div className="mt-3 flex justify-center">
                <Button variant="outline" size="sm" onClick={() => setPage(1)}>
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
                  <TableHead className="pr-3">{t("columns.status")}</TableHead>
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
                      <TableCell className="pr-3">
                        <Badge
                          variant={archived ? "muted" : "secondary"}
                          data-status={archived ? "archived" : "active"}
                        >
                          {archived ? status("archived") : status("active")}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            {pageInfo === null ? null : (
              <PerformancePagination
                page={pageInfo}
                label={t("paginationLabel")}
                onPageChange={setPage}
              />
            )}
          </>
        )}
      </Card>
    </section>
  );
}
