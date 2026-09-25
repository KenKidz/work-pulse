"use client";

/**
 * Locations settings table — loading/empty/error states, `include_archived`
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

import { useLocationArchive, useLocations, useLocationRestore } from "../hooks/use-locations";
import type { Location } from "../types";
import { LocationFormDialog } from "./LocationFormDialog";

export function LocationsTable() {
  const [includeArchived, setIncludeArchived] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Location | null>(null);

  const locationsQuery = useLocations({ include_archived: includeArchived, page_size: 100 });
  const archiveLocation = useLocationArchive();
  const restoreLocation = useLocationRestore();

  const listResponse = locationsQuery.data;
  const locations: Location[] =
    listResponse && listResponse.status === 200 ? listResponse.data.items : [];

  function openCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function openEdit(location: Location) {
    setEditing(location);
    setDialogOpen(true);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <Button onClick={openCreate}>
          <Plus />
          New location
        </Button>
        <Button
          variant="outline"
          aria-pressed={includeArchived}
          onClick={() => setIncludeArchived((value) => !value)}
        >
          Include archived
        </Button>
      </div>

      {locationsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">Loading locations…</p>
      ) : locationsQuery.isError ? (
        <div role="alert" className="rounded-md border border-destructive/40 p-4 text-sm">
          <p>Could not load locations.</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => void locationsQuery.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : locations.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No locations yet. Create the first one with its IANA timezone.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Address</TableHead>
              <TableHead>Timezone</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {locations.map((location) => (
              <TableRow key={location.id}>
                <TableCell className="font-medium">{location.name}</TableCell>
                <TableCell className="max-w-64 truncate text-muted-foreground">
                  {location.address ?? "—"}
                </TableCell>
                <TableCell>{location.timezone}</TableCell>
                <TableCell>{location.archived_at ? "Archived" : "Active"}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="xs"
                      aria-label={`Edit ${location.name}`}
                      onClick={() => openEdit(location)}
                    >
                      <Pencil />
                      Edit
                    </Button>
                    {location.archived_at ? (
                      <Button
                        variant="ghost"
                        size="xs"
                        aria-label={`Restore ${location.name}`}
                        disabled={restoreLocation.isPending}
                        onClick={() => restoreLocation.mutate({ id: location.id })}
                      >
                        <ArchiveRestore />
                        Restore
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        size="xs"
                        aria-label={`Archive ${location.name}`}
                        disabled={archiveLocation.isPending}
                        onClick={() => archiveLocation.mutate({ id: location.id })}
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

      <LocationFormDialog
        key={editing?.id ?? "new"}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        location={editing}
      />
    </div>
  );
}
