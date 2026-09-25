"use client";

/**
 * Departments settings table — loading/empty/error states, `include_archived`
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

import {
  useDepartmentArchive,
  useDepartments,
  useDepartmentRestore,
} from "../hooks/use-departments";
import type { Department } from "../types";
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
      <div className="flex items-center justify-between gap-2">
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

      {departmentsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">Loading departments…</p>
      ) : departmentsQuery.isError ? (
        <div role="alert" className="rounded-md border border-destructive/40 p-4 text-sm">
          <p>Could not load departments.</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => void departmentsQuery.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : departments.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No departments yet. Create the first one to structure your organization.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Parent</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {departments.map((department) => (
              <TableRow key={department.id}>
                <TableCell className="font-medium">{department.name}</TableCell>
                <TableCell>
                  {department.parent_department_id
                    ? (nameById.get(department.parent_department_id) ?? "—")
                    : "—"}
                </TableCell>
                <TableCell>{department.archived_at ? "Archived" : "Active"}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="xs"
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
      )}

      <DepartmentFormDialog
        key={editing?.id ?? "new"}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        department={editing}
      />
    </div>
  );
}
