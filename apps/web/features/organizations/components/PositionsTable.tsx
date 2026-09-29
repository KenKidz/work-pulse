"use client";

/**
 * Positions settings table — loading/empty/error states, `include_archived`
 * toggle, and row actions Edit / Archive / Restore. Client leaf.
 */

import { Archive, ArchiveRestore, Briefcase, CircleAlert, Pencil, Plus } from "lucide-react";
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
          New position
        </Button>
        <Button
          variant="outline"
          aria-pressed={includeArchived}
          onClick={() => setIncludeArchived((value) => !value)}
        >
          Include archived
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden py-0">
        {positionsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading positions…</p>
          </div>
        ) : positionsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load positions.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void positionsQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : positions.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Briefcase}
              message="No positions yet. Create the first one to describe your job titles."
            />
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-3">Title</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="pr-3 text-right">Actions</TableHead>
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
                        aria-label={`Edit ${position.title}`}
                        onClick={() => openEdit(position)}
                      >
                        <Pencil />
                        Edit
                      </Button>
                      {position.archived_at ? (
                        <Button
                          variant="ghost"
                          size="xs"
                          aria-label={`Restore ${position.title}`}
                          disabled={restorePosition.isPending}
                          onClick={() => restorePosition.mutate({ id: position.id })}
                        >
                          <ArchiveRestore />
                          Restore
                        </Button>
                      ) : (
                        <Button
                          variant="ghost"
                          size="xs"
                          aria-label={`Archive ${position.title}`}
                          disabled={archivePosition.isPending}
                          onClick={() => archivePosition.mutate({ id: position.id })}
                        >
                          <Archive />
                          Archive
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
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
