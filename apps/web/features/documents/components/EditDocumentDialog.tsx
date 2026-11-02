"use client";

/**
 * Edit a document's metadata (title, category, expiry). This is a field
 * update, not a state transition: the API audits it and emits no event. The
 * dialog sends only the fields that actually changed. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Form } from "@/components/ui/form";

import { useApiErrorTranslator } from "@/lib/api-errors";

import { useDocumentUpdate } from "../hooks/use-documents";
import { documentsErrorMessage } from "../labels";
import { documentMetadataSchema, type DocumentMetadataValues } from "../schema";
import type { Document, DocumentCategory, DocumentUpdate } from "../types";
import { DocumentMetadataFields } from "./DocumentMetadataFields";

type EditDocumentDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employeeId: string;
  document: Document;
};

export function EditDocumentDialog({
  open,
  onOpenChange,
  employeeId,
  document,
}: EditDocumentDialogProps) {
  const t = useTranslations("documents.edit");
  const validation = useTranslations("documents.validation");
  const errors = useApiErrorTranslator("documents.errors");
  const common = useTranslations("common.actions");
  const updateDocument = useDocumentUpdate(employeeId);
  const [rootError, setRootError] = useState<string | null>(null);

  const form = useForm<DocumentMetadataValues>({
    resolver: zodResolver(documentMetadataSchema((key) => validation(key))),
    defaultValues: {
      title: document.title,
      category: document.category,
      expiry_date: document.expiry_date ?? "",
    },
  });

  async function onSubmit(values: DocumentMetadataValues) {
    setRootError(null);
    const expiryDate = values.expiry_date === "" ? null : values.expiry_date;
    const patch: DocumentUpdate = {};
    if (values.title !== document.title) patch.title = values.title;
    if (values.category !== document.category) {
      patch.category = values.category as DocumentCategory;
    }
    if (expiryDate !== (document.expiry_date ?? null)) patch.expiry_date = expiryDate;

    if (Object.keys(patch).length === 0) {
      onOpenChange(false);
      return;
    }

    try {
      await updateDocument.mutateAsync({
        employeeId,
        documentId: document.id,
        data: patch,
      });
      onOpenChange(false);
    } catch (error) {
      setRootError(documentsErrorMessage(error, errors));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description", { title: document.title })}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <DocumentMetadataFields />

            {rootError ? (
              <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {rootError}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={updateDocument.isPending || !form.formState.isDirty}>
                {updateDocument.isPending ? t("saving") : t("save")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
