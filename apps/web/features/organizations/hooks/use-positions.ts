/**
 * Position data hooks — thin wrappers over the generated client hooks.
 * Mutations invalidate the URL-keyed list cache, toast the outcome, and map
 * `ApiError.code` back to form field errors.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useArchivePosition,
  useCreatePosition,
  useListPositions,
  useRestorePosition,
  useUpdatePosition,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidatePositions } from "../api";
import type { ListPositionsParams } from "../types";
import { errorMessage } from "./use-departments";

export function usePositions(params?: ListPositionsParams) {
  return useListPositions(params);
}

export type PositionFieldName = "title" | "description";
export type PositionFieldErrors = Partial<Record<PositionFieldName | "root", string>>;

type PositionErrorTranslator = (
  key: "positionTitleTaken" | "forbidden" | "generic",
) => string;

/** Map a stable API error code to the field it belongs to. */
export function positionFieldErrors(
  error: unknown,
  t: PositionErrorTranslator,
): PositionFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "position_title_taken":
        return { title: t("positionTitleTaken") };
      case "forbidden":
        return { root: t("forbidden") };
      default:
        return { root: error.message };
    }
  }
  return { root: t("generic") };
}

export function usePositionCreate() {
  const t = useTranslations("organizations.toasts");
  const errors = useTranslations("organizations.errors");
  const queryClient = useQueryClient();
  return useCreatePosition({
    mutation: {
      onSuccess: async () => {
        await invalidatePositions(queryClient);
        toast.success(t("positionCreated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function usePositionUpdate() {
  const t = useTranslations("organizations.toasts");
  const errors = useTranslations("organizations.errors");
  const queryClient = useQueryClient();
  return useUpdatePosition({
    mutation: {
      onSuccess: async () => {
        await invalidatePositions(queryClient);
        toast.success(t("positionUpdated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function usePositionArchive() {
  const t = useTranslations("organizations.toasts");
  const errors = useTranslations("organizations.errors");
  const queryClient = useQueryClient();
  return useArchivePosition({
    mutation: {
      onSuccess: async () => {
        await invalidatePositions(queryClient);
        toast.success(t("positionArchived"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function usePositionRestore() {
  const t = useTranslations("organizations.toasts");
  const errors = useTranslations("organizations.errors");
  const queryClient = useQueryClient();
  return useRestorePosition({
    mutation: {
      onSuccess: async () => {
        await invalidatePositions(queryClient);
        toast.success(t("positionRestored"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}
