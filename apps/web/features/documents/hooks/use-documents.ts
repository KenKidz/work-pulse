"use client";

/**
 * Documents data hooks — thin wrappers over the generated client hooks.
 *
 * The API is the authority for read scope; the components only decide which
 * controls render. Every mutation invalidates its URL-keyed lists so a change
 * lands without a reload, and `ApiError.code` maps back to the dialog that
 * caused it. Downloads call the endpoint imperatively so every click mints a
 * fresh presigned URL — a query hook would prefetch and cache it.
 */

import { useCallback } from "react";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import {
  downloadDocument,
  useCreateDocument,
  useCreateDocumentUpload,
  useDeleteDocument,
  useListDocuments,
  useUpdateDocument,
} from "@workpulse/api-client";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidateDocuments } from "../api";
import { documentsErrorMessage } from "../labels";
import type { ListDocumentsParams } from "../types";

/**
 * The employee's visible documents. Keeping previous data through a page or
 * category change stops the table collapsing to a placeholder on every click.
 */
export function useDocuments(employeeId: string, params?: ListDocumentsParams) {
  return useListDocuments(employeeId, params, {
    query: { placeholderData: keepPreviousData },
  });
}

/** Step 1: request a presigned staging upload. No toast — the dialog reports. */
export function useDocumentUploadIntent() {
  return useCreateDocumentUpload();
}

/** Step 3: confirm the staged object, then toast and refresh the list. */
export function useDocumentCreate(employeeId: string) {
  const t = useTranslations("documents.toasts");
  const errors = useTranslations("documents.errors");
  const queryClient = useQueryClient();
  return useCreateDocument({
    mutation: {
      onSuccess: async () => {
        await invalidateDocuments(queryClient, employeeId);
        toast.success(t("uploaded"));
      },
      onError: (error) => toast.error(documentsErrorMessage(error, errors)),
    },
  });
}

export function useDocumentUpdate(employeeId: string) {
  const t = useTranslations("documents.toasts");
  const errors = useTranslations("documents.errors");
  const queryClient = useQueryClient();
  return useUpdateDocument({
    mutation: {
      onSuccess: async () => {
        await invalidateDocuments(queryClient, employeeId);
        toast.success(t("updated"));
      },
      onError: (error) => toast.error(documentsErrorMessage(error, errors)),
    },
  });
}

export function useDocumentDelete(employeeId: string) {
  const t = useTranslations("documents.toasts");
  const errors = useTranslations("documents.errors");
  const queryClient = useQueryClient();
  return useDeleteDocument({
    mutation: {
      onSuccess: async () => {
        await invalidateDocuments(queryClient, employeeId);
        toast.success(t("deleted"));
      },
      onError: (error) => toast.error(documentsErrorMessage(error, errors)),
    },
  });
}

/**
 * Mint a fresh presigned GET URL per click and open it in a new tab. Nothing
 * is cached server-side or prefetched; a failure becomes a toast, never a
 * stale URL left on screen.
 */
export function useDocumentDownload(employeeId: string) {
  const errors = useTranslations("documents.errors");

  return useCallback(
    async (documentId: string): Promise<void> => {
      try {
        const response = await downloadDocument(employeeId, documentId);
        if (response.status === 200) {
          window.open(response.data.url, "_blank", "noopener,noreferrer");
        }
      } catch (error) {
        toast.error(documentsErrorMessage(error, errors));
      }
    },
    [employeeId, errors],
  );
}
