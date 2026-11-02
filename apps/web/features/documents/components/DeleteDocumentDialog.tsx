"use client";

/**
 * Delete a document after confirmation. The row is soft-deleted and the object
 * is purged; a second delete is a 404, so the dialog closes only on success.
 * Client leaf.
 */

import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
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

import { useApiErrorTranslator } from "@/lib/api-errors";

import { useDocumentDelete } from "../hooks/use-documents";
import { documentsErrorMessage } from "../labels";
import type { Document } from "../types";

type DeleteDocumentDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employeeId: string;
  document: Document;
};

export function DeleteDocumentDialog({
  open,
  onOpenChange,
  employeeId,
  document,
}: DeleteDocumentDialogProps) {
  const t = useTranslations("documents.delete");
  const errors = useApiErrorTranslator("documents.errors");
  const common = useTranslations("common.actions");
  const removeDocument = useDocumentDelete(employeeId);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setError(null);
    try {
      await removeDocument.mutateAsync({ employeeId, documentId: document.id });
      onOpenChange(false);
    } catch (failure) {
      setError(documentsErrorMessage(failure, errors));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description", { title: document.title })}</DialogDescription>
        </DialogHeader>

        {error ? (
          <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {common("cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={removeDocument.isPending}
            onClick={() => void confirm()}
          >
            {removeDocument.isPending ? t("deleting") : t("action")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
