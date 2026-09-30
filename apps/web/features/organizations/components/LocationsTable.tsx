"use client";

/**
 * Locations settings table — loading/empty/error states, `include_archived`
 * toggle, and row actions Edit / Archive / Restore. Client leaf.
 */

import { Archive, ArchiveRestore, CircleAlert, MapPin, Pencil, Plus } from "lucide-react";
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

import { useLocationArchive, useLocations, useLocationRestore } from "../hooks/use-locations";
import type { Location } from "../types";
import { ArchiveStatusBadge } from "./ArchiveStatusBadge";
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
      <div className="flex flex-wrap items-center justify-between gap-2">
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

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {locationsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading locations…</p>
          </div>
        ) : locationsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load locations.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void locationsQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : locations.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={MapPin}
              message="No locations yet. Create the first one with its IANA timezone."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every location field.
            </p>
            <Table
              aria-label="Locations"
              containerLabel="Locations. Scroll horizontally to see every location field."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Name</TableHead>
                  <TableHead>Address</TableHead>
                  <TableHead>Timezone</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {locations.map((location) => (
                  <TableRow key={location.id}>
                    <TableCell className="pl-3 font-medium">{location.name}</TableCell>
                    <TableCell className="max-w-64 truncate text-muted-foreground">
                      {location.address ?? "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{location.timezone}</TableCell>
                    <TableCell>
                      <ArchiveStatusBadge archivedAt={location.archived_at} />
                    </TableCell>
                    <TableCell className="pr-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="xs"
                          className="min-h-11 sm:min-h-7"
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
                            className="min-h-11 sm:min-h-7"
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
                            className="min-h-11 sm:min-h-7"
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
          </>
        )}
      </Card>

      <LocationFormDialog
        key={editing?.id ?? "new"}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        location={editing}
      />
    </div>
  );
}
