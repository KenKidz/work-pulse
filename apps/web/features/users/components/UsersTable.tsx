"use client";

/**
 * Users management — filter bar (`q`, role, account status), status badges,
 * pagination and per-row actions (edit, reset password, deactivate/activate).
 * Loading/empty/error states per the pinned AC12. Admin-only: the signed-in
 * role comes from `useAuth()`; a non-admin visit redirects to `/dashboard`
 * (the page stays unreachable — the API policy is the authority). Client leaf.
 */

import { CircleAlert, Search, UserPlus, Users } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

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

import { useAuth } from "@/features/auth/components/AuthGate";
import { MANAGE_USER_ROLES } from "@/components/destinations";

import { useUserActivate, useUsers } from "../hooks/use-users";
import type { ListUsersParams, Role, User } from "../types";
import { DeactivateDialog } from "./DeactivateDialog";
import { ResetPasswordDialog } from "./ResetPasswordDialog";
import { UserFormDialog } from "./UserFormDialog";
import { ALL_ROLES, accountStatusLabel, accountStatusVariant, roleLabel } from "./labels";

export const ANY = "any";

/** Page size for the users list query. */
export const USERS_PAGE_SIZE = 20;

/**
 * Filter bar. Clearing a filter must drop the parameter (`undefined`), never
 * send `null`: the generated client only omits undefined and serializes null
 * as the literal string "null" → the API 422s on parsing.
 */
function FiltersRow({
  params,
  onChange,
}: {
  params: ListUsersParams;
  onChange: (next: Partial<ListUsersParams>) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="user-filters">
      <div className="relative w-full sm:w-64">
        <Search className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label="Search users"
          placeholder="Search email"
          className="w-full pl-8"
          value={params.q ?? ""}
          onChange={(event) => onChange({ q: event.target.value || undefined })}
        />
      </div>

      <Select
        value={params.role ?? ANY}
        onValueChange={(value) =>
          onChange({ role: value === ANY ? undefined : (value as Role), page: 1 })
        }
      >
        <SelectTrigger aria-label="Filter by role" className="w-full sm:w-44">
          <SelectValue placeholder="All roles" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All roles</SelectItem>
          {ALL_ROLES.map((role) => (
            <SelectItem key={role} value={role}>
              {roleLabel[role]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={params.active === undefined ? ANY : params.active ? "active" : "inactive"}
        onValueChange={(value) =>
          onChange({
            active: value === "active" ? true : value === "inactive" ? false : undefined,
            page: 1,
          })
        }
      >
        <SelectTrigger aria-label="Filter by status" className="w-full sm:w-40">
          <SelectValue placeholder="All statuses" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>All statuses</SelectItem>
          <SelectItem value="active">Active</SelectItem>
          <SelectItem value="inactive">Inactive</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

type UsersTableState = {
  /** Opens the edit dialog for a user (or create when `null`). */
  editing: User | null;
  formOpen: boolean;
  /** The user of the open deactivate confirm, if any. */
  deactivating: User | null;
  /** The user of the open reset-password dialog, if any. */
  resetting: User | null;
};

export function UsersTable() {
  const router = useRouter();
  const { user } = useAuth();
  const [params, setParams] = useState<ListUsersParams>({ page: 1, page_size: USERS_PAGE_SIZE });
  // The search box state — fed to the query through useDebouncedValue so a
  // typed email costs one list request on a 300 ms pause, not one per
  // keystroke. Typing resets the page immediately; only the API call waits.
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebouncedValue(searchInput);
  const [state, setState] = useState<UsersTableState>({
    editing: null,
    formOpen: false,
    deactivating: null,
    resetting: null,
  });

  const activate = useUserActivate();

  const allowed = user !== null && MANAGE_USER_ROLES.includes(user.role);

  useEffect(() => {
    // Direct visits by non-admins never render the surface (AC12's
    // "unreachable"); the users API would 403 anyway.
    if (user !== null && !allowed) {
      router.replace("/dashboard");
    }
  }, [user, allowed, router]);

  const usersQuery = useUsers({ ...params, q: debouncedSearch || undefined });
  const listResponse = usersQuery.data;
  const users = listResponse && listResponse.status === 200 ? listResponse.data.items : [];
  const page = listResponse && listResponse.status === 200 ? listResponse.data.page : null;

  function change(next: Partial<ListUsersParams>) {
    // The q input goes through the debounce; selects commit directly.
    if ("q" in next) {
      setSearchInput(next.q ?? "");
      setParams((current) => (current.page === 1 ? current : { ...current, page: 1 }));
      return;
    }
    setParams((current) => ({ ...current, ...next }));
  }

  if (user === null || !allowed) {
    // Admin session still resolving, or the redirect above is in flight.
    return (
      <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
        <Spinner className="size-4" />
        <p>Checking access…</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <FiltersRow params={{ ...params, q: searchInput }} onChange={change} />
        <Button
          className="ml-auto"
          onClick={() =>
            setState({ editing: null, formOpen: true, deactivating: null, resetting: null })
          }
        >
          <UserPlus />
          New user
        </Button>
      </div>

      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        {usersQuery.isPending ? (
          <div className="flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground">
            <Spinner className="size-4" />
            <p>Loading users…</p>
          </div>
        ) : usersQuery.isError ? (
          <div className="p-4">
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>Could not load users.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void usersQuery.refetch()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : users.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={Users}
              message="No users match. Create the first account to give someone access."
            />
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every user field.
            </p>
            <Table
              aria-label="Users"
              containerLabel="Users. Scroll horizontally to read all columns."
            >
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Employee</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="pr-3 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="pl-3 font-medium">{row.email}</TableCell>
                    <TableCell>{roleLabel[row.role]}</TableCell>
                    <TableCell>
                      {row.employee_id ? (
                        <span className="text-sm text-muted-foreground">
                          Employee …{row.employee_id.slice(-4)}
                        </span>
                      ) : (
                        <span className="text-sm text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={accountStatusVariant(row.active)}
                        data-status={row.active ? "active" : "inactive"}
                      >
                        {accountStatusLabel(row.active)}
                      </Badge>
                    </TableCell>
                    <TableCell className="pr-3 text-right">
                      <div className="inline-flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="xs"
                          className="min-h-11 sm:min-h-7"
                          aria-label={`Edit ${row.email}`}
                          onClick={() =>
                            setState({
                              editing: row,
                              formOpen: true,
                              deactivating: null,
                              resetting: null,
                            })
                          }
                        >
                          Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="xs"
                          className="min-h-11 sm:min-h-7"
                          aria-label={`Reset password for ${row.email}`}
                          onClick={() =>
                            setState({
                              editing: null,
                              formOpen: false,
                              deactivating: null,
                              resetting: row,
                            })
                          }
                        >
                          Reset password
                        </Button>
                        {row.active ? (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={`Deactivate ${row.email}`}
                            onClick={() =>
                              setState({
                                editing: null,
                                formOpen: false,
                                deactivating: row,
                                resetting: null,
                              })
                            }
                          >
                            Deactivate
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="min-h-11 sm:min-h-7"
                            aria-label={`Activate ${row.email}`}
                            disabled={activate.isPending}
                            onClick={() =>
                              void activate.mutateAsync({ id: row.id }).catch(() => undefined)
                            }
                          >
                            Activate
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {page && page.total_pages > 1 ? (
              <div className="flex items-center justify-end gap-2 border-t px-4 py-3 text-sm">
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Previous page"
                  disabled={page.page <= 1}
                  onClick={() => change({ page: page.page - 1 })}
                >
                  Previous
                </Button>
                <span className="text-muted-foreground">
                  Page {page.page} of {page.total_pages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Next page"
                  disabled={page.page >= page.total_pages}
                  onClick={() => change({ page: page.page + 1 })}
                >
                  Next
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Card>

      <UserFormDialog
        key={state.editing?.id ?? "new"}
        open={state.formOpen}
        onOpenChange={(formOpen) => setState((current) => ({ ...current, formOpen }))}
        user={state.editing}
      />
      <DeactivateDialog
        open={state.deactivating !== null}
        onOpenChange={(open) =>
          setState((current) => (open ? current : { ...current, deactivating: null }))
        }
        user={state.deactivating}
      />
      <ResetPasswordDialog
        open={state.resetting !== null}
        onOpenChange={(open) =>
          setState((current) => (open ? current : { ...current, resetting: null }))
        }
        user={state.resetting}
      />
    </div>
  );
}
