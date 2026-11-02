"use client";

/**
 * Two-step document upload: request a presigned staging POST, upload the file
 * straight to private storage, then confirm the metadata. The row only exists
 * after the confirm call succeeds; every failure keeps the dialog open with a
 * user-facing sentence. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";

import { useApiErrorTranslator } from "@/lib/api-errors";

import { uploadStagedObject } from "../api";
import { useDocumentCreate, useDocumentUploadIntent } from "../hooks/use-documents";
import { documentsErrorMessage } from "../labels";
import {
  DOCUMENT_CONTENT_TYPES,
  documentUploadSchema,
  type DocumentContentType,
  type DocumentUploadValues,
} from "../schema";
import type { DocumentCategory } from "../types";
import { DocumentMetadataFields } from "./DocumentMetadataFields";

type UploadDocumentDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employeeId: string;
};

const EMPTY_FORM: DocumentUploadValues = {
  file: null,
  title: "",
  category: "",
  expiry_date: "",
};

export function UploadDocumentDialog({
  open,
  onOpenChange,
  employeeId,
}: UploadDocumentDialogProps) {
  const t = useTranslations("documents.upload");
  const errors = useApiErrorTranslator("documents.errors");
  const validation = useTranslations("documents.validation");
  const common = useTranslations("common.actions");
  const intent = useDocumentUploadIntent();
  const createDocument = useDocumentCreate(employeeId);
  const [uploading, setUploading] = useState(false);
  const [rootError, setRootError] = useState<string | null>(null);
  const [controller, setController] = useState<AbortController | null>(null);

  const form = useForm<DocumentUploadValues>({
    resolver: zodResolver(documentUploadSchema((key) => validation(key))),
    defaultValues: EMPTY_FORM,
  });
  const { reset } = form;
  const busy = uploading || intent.isPending || createDocument.isPending;

  useEffect(() => {
    // Leaving the surface aborts the in-flight storage POST.
    return () => controller?.abort();
  }, [controller]);

  function resetState() {
    // The dialog stays mounted while closed, so clear the two-step state
    // before the next open instead of resetting inside an effect.
    reset(EMPTY_FORM);
    setRootError(null);
  }

  function handleOpenChange(next: boolean) {
    if (!next) {
      // Closing abandons this flow: abort the storage POST and clear the form.
      // Abandoned uploads are safe (no row, no audit entry).
      controller?.abort();
      setController(null);
      setUploading(false);
      resetState();
    }
    onOpenChange(next);
  }

  async function onSubmit(values: DocumentUploadValues) {
    setRootError(null);
    const file = values.file;
    if (file === null) return;

    const flow = new AbortController();
    setController(flow);
    const abandoned = () => flow.signal.aborted;

    try {
      setUploading(true);
      const intentResponse = await intent.mutateAsync({
        employeeId,
        data: {
          content_type: file.type as DocumentContentType,
          size_bytes: file.size,
        },
      });
      if (abandoned()) return;
      if (intentResponse.status !== 201) {
        throw new Error("The upload intent was not created");
      }

      await uploadStagedObject(intentResponse.data, file, flow.signal);
      if (abandoned()) return;
      await createDocument.mutateAsync({
        employeeId,
        data: {
          upload_id: intentResponse.data.upload_id,
          title: values.title,
          category: values.category as DocumentCategory,
          expiry_date: values.expiry_date === "" ? null : values.expiry_date,
        },
      });
      if (abandoned()) return;
      resetState();
      onOpenChange(false);
    } catch (error) {
      if (abandoned()) return;
      // A storage POST failure is not an ApiError: keep the pinned upload copy.
      setRootError(
        error instanceof ApiError ? documentsErrorMessage(error, errors) : t("uploadFailed"),
      );
    } finally {
      if (!abandoned()) {
        setController(null);
        setUploading(false);
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="file"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("fileHint")}>{t("file")}</FormLabel>
                  <FormControl>
                    <Input
                      type="file"
                      name={field.name}
                      ref={field.ref}
                      onBlur={field.onBlur}
                      accept={DOCUMENT_CONTENT_TYPES.join(",")}
                      onChange={(event) => field.onChange(event.target.files?.[0] ?? null)}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DocumentMetadataFields titlePlaceholder={t("titlePlaceholder")} />

            {rootError ? (
              <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {rootError}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? t("submitting") : t("submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
