"use client";

/**
 * Departments settings table — loading/empty/error states, `include_archived`
 * toggle, and row actions Edit / Archive / Restore. Client leaf.
 */

import { Archive, ArchiveRestore, Building2, CircleAlert, Pencil, Plus } from "lucide-react";
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

import {
  useDepartmentArchive,
  useDepartments,
  useDepartmentRestore,
} from "../hooks/use-departments";
import type { Department } from "../types";
import { ArchiveStatusBadge } from "./ArchiveStatusBadge";
import { DepartmentFormDialog } from "./DepartmentFormDialog";

export function DepartmentsTable() {
  const [includeArchived, setIncludeArchived] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Department | null>(null);

  const departmentsQuery = useDepartments({
    include_archived: includeArchived,
    page_size: 100,
  });
  const archiveDepartment = useDepartmentArchive();
  const restoreDepartment = useDepartmentRestore();

  const listResponse = departmentsQuery.data;
  const departments: Department[] =
    listResponse && listResponse.status === 200 ? listResponse.data.items : [];
  const nameById = new Map(departments.map((department) => [department.id, department.name]));

  function openCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function openEdit(department: Department) {
    setEditing(department);
    setDialogOpen(true);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button onClick={openCreate}>
          <Plus />
          New department
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
        {departmentsQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading departments…</p>
          </div>
        ) : departmentsQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load departments.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void departmentsQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : departments.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Building2}
              message="No departments yet. Create the first one to structure your organization."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every department field.
            </p>
            <Table
              aria-label="Departments"
              containerLabel="Departments. Scroll horizontally to see every department field."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Name</TableHead>
                  <TableHead>Parent</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {departments.map((department) => (
                  <TableRow key={department.id}>
                    <TableCell className="pl-3 font-medium">{department.name}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {department.parent_department_id
                        ? (nameById.get(department.parent_department_id) ?? "—")
                        : "—"}
                    </TableCell>
                    <TableCell>
                      <ArchiveStatusBadge archivedAt={department.archived_at} />
                    </TableCell>
                    <TableCell className="pr-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="xs"
                          className="min-h-11 sm:min-h-7"
                          aria-label={`Edit ${department.name}`}
                          onClick={() => openEdit(department)}
                        >
                          <Pencil />
                          Edit
                        </Button>
                        {department.archived_at ? (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={`Restore ${department.name}`}
                            disabled={restoreDepartment.isPending}
                            onClick={() => restoreDepartment.mutate({ id: department.id })}
                          >
                            <ArchiveRestore />
                            Restore
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={`Archive ${department.name}`}
                            disabled={archiveDepartment.isPending}
                            onClick={() => archiveDepartment.mutate({ id: department.id })}
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

      <DepartmentFormDialog
        key={editing?.id ?? "new"}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        department={editing}
      />
    </div>
  );
}
