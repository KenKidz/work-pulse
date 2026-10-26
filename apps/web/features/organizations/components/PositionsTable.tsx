"use client";

/**
 * Positions settings table — loading/empty/error states, `include_archived`
 * toggle, and row actions Edit / Archive / Restore. Client leaf.
 */

import { Archive, ArchiveRestore, Briefcase, CircleAlert, Pencil, Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
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

import { usePositionArchive, usePositions, usePositionRestore } from "../hooks/use-positions";
import type { Position } from "../types";
import { ArchiveStatusBadge } from "./ArchiveStatusBadge";
import { PositionFormDialog } from "./PositionFormDialog";

export function PositionsTable() {
  const t = useTranslations();
  const [includeArchived, setIncludeArchived] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Position | null>(null);

  const positionsQuery = usePositions({ include_archived: includeArchived, page_size: 100 });
  const archivePosition = usePositionArchive();
  const restorePosition = usePositionRestore();

  const listResponse = positionsQuery.data;
  const positions: Position[] =
    listResponse && listResponse.status === 200 ? listResponse.data.items : [];

  function openCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function openEdit(position: Position) {
    setEditing(position);
    setDialogOpen(true);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button onClick={openCreate}>
          <Plus />
          {t("organizations.actions.newPosition")}
        </Button>
        <Button
          variant="outline"
          aria-pressed={includeArchived}
          onClick={() => setIncludeArchived((value) => !value)}
        >
          {t("organizations.actions.includeArchived")}
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {positionsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>{t("organizations.positions.loading")}</p>
          </div>
        ) : positionsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{t("organizations.positions.loadError")}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void positionsQuery.refetch()}
                >
                  {t("common.actions.retry")}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : positions.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Briefcase}
              message={t("organizations.positions.empty")}
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              {t("organizations.positions.swipe")}
            </p>
            <Table
              aria-label={t("organizations.positions.tableLabel")}
              containerLabel={t("organizations.positions.containerLabel")}
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("organizations.positions.columns.title")}</TableHead>
                  <TableHead>{t("organizations.positions.columns.description")}</TableHead>
                  <TableHead>{t("organizations.positions.columns.status")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("organizations.positions.columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {positions.map((position) => (
                  <TableRow key={position.id}>
                    <TableCell className="pl-3 font-medium">{position.title}</TableCell>
                    <TableCell className="max-w-64 truncate text-muted-foreground">
                      {position.description ?? "—"}
                    </TableCell>
                    <TableCell>
                      <ArchiveStatusBadge archivedAt={position.archived_at} />
                    </TableCell>
                    <TableCell className="pr-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="xs"
                          className="min-h-11 sm:min-h-7"
                          aria-label={t("organizations.positions.editAria", { title: position.title })}
                          onClick={() => openEdit(position)}
                        >
                          <Pencil />
                          {t("common.actions.edit")}
                        </Button>
                        {position.archived_at ? (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={t("organizations.positions.restoreAria", { title: position.title })}
                            disabled={restorePosition.isPending}
                            onClick={() => restorePosition.mutate({ id: position.id })}
                          >
                            <ArchiveRestore />
                            {t("common.actions.restore")}
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={t("organizations.positions.archiveAria", { title: position.title })}
                            disabled={archivePosition.isPending}
                            onClick={() => archivePosition.mutate({ id: position.id })}
                          >
                            <Archive />
                            {t("common.actions.archive")}
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </Card>

      <PositionFormDialog
        key={editing?.id ?? "new"}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        position={editing}
      />
    </div>
  );
}
