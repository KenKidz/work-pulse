"use client";

/**
 * Positions settings table — loading/empty/error states, `include_archived`
 * toggle, and row actions Edit / Archive / Restore. Client leaf.
 */

import { Archive, ArchiveRestore, Pencil, Plus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
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
      <div className="flex items-center justify-between gap-2">
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

      {positionsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">Loading positions…</p>
      ) : positionsQuery.isError ? (
        <div role="alert" className="rounded-md border border-destructive/40 p-4 text-sm">
          <p>Could not load positions.</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => void positionsQuery.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : positions.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No positions yet. Create the first one to describe your job titles.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {positions.map((position) => (
              <TableRow key={position.id}>
                <TableCell className="font-medium">{position.title}</TableCell>
                <TableCell className="max-w-64 truncate text-muted-foreground">
                  {position.description ?? "—"}
                </TableCell>
                <TableCell>{position.archived_at ? "Archived" : "Active"}</TableCell>
                <TableCell className="text-right">
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

      <PositionFormDialog
        key={editing?.id ?? "new"}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        position={editing}
      />
    </div>
  );
}
