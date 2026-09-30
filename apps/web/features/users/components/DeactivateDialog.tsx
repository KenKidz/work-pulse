"use client";

/**
 * Deactivation confirm dialog — deactivating a user blocks sign-in
 * immediately and stops their sessions from resolving. The pinned `last_admin`
 * 409 renders as a visible root error and the dialog stays open (AC12).
 * Client leaf.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { useUserDeactivate, userFieldErrors } from "../hooks/use-users";
import type { User } from "../types";

type DeactivateDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The user being deactivated; `null` while the dialog is closed. */
  user: User | null;
};

export function DeactivateDialog({ open, onOpenChange, user }: DeactivateDialogProps) {
  const [rootError, setRootError] = useState<string | null>(null);
  const deactivate = useUserDeactivate();

  async function onConfirm() {
    if (user === null) return;
    setRootError(null);
    try {
      await deactivate.mutateAsync({ id: user.id });
      onOpenChange(false);
    } catch (error) {
      // Deactivation errors are root-level: there is nothing to fix per-field.
      setRootError(userFieldErrors(error).root ?? null);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setRootError(null);
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Deactivate user</DialogTitle>
          <DialogDescription>
            {user?.email} loses access immediately — sign-in is rejected and every session stops
            resolving. Their account and history are kept; activation restores access.
          </DialogDescription>
        </DialogHeader>

        {rootError ? (
          <p role="alert" className="text-sm text-destructive">
            {rootError}
          </p>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={deactivate.isPending}
            onClick={() => void onConfirm()}
          >
            Deactivate
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
