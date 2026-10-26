/**
 * Location data hooks — thin wrappers over the generated client hooks.
 * Mutations invalidate the URL-keyed list cache, toast the outcome, and map
 * `ApiError.code` back to form field errors (`invalid_timezone` → timezone).
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useArchiveLocation,
  useCreateLocation,
  useListLocations,
  useRestoreLocation,
  useUpdateLocation,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidateLocations } from "../api";
import type { ListLocationsParams } from "../types";
import { errorMessage } from "./use-departments";

export function useLocations(params?: ListLocationsParams) {
  return useListLocations(params);
}

export type LocationFieldName = "name" | "address" | "timezone";
export type LocationFieldErrors = Partial<Record<LocationFieldName | "root", string>>;

type LocationErrorTranslator = (
  key: "locationNameTaken" | "invalidTimezone" | "forbidden" | "generic",
) => string;

/** Map a stable API error code to the field it belongs to. */
export function locationFieldErrors(
  error: unknown,
  t: LocationErrorTranslator,
): LocationFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "location_name_taken":
        return { name: t("locationNameTaken") };
      case "invalid_timezone":
        return { timezone: t("invalidTimezone") };
      case "forbidden":
        return { root: t("forbidden") };
      default:
        return { root: error.message };
    }
  }
  return { root: t("generic") };
}

export function useLocationCreate() {
  const t = useTranslations("organizations.toasts");
  const errors = useTranslations("organizations.errors");
  const queryClient = useQueryClient();
  return useCreateLocation({
    mutation: {
      onSuccess: async () => {
        await invalidateLocations(queryClient);
        toast.success(t("locationCreated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useLocationUpdate() {
  const t = useTranslations("organizations.toasts");
  const errors = useTranslations("organizations.errors");
  const queryClient = useQueryClient();
  return useUpdateLocation({
    mutation: {
      onSuccess: async () => {
        await invalidateLocations(queryClient);
        toast.success(t("locationUpdated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useLocationArchive() {
  const t = useTranslations("organizations.toasts");
  const errors = useTranslations("organizations.errors");
  const queryClient = useQueryClient();
  return useArchiveLocation({
    mutation: {
      onSuccess: async () => {
        await invalidateLocations(queryClient);
        toast.success(t("locationArchived"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useLocationRestore() {
  const t = useTranslations("organizations.toasts");
  const errors = useTranslations("organizations.errors");
  const queryClient = useQueryClient();
  return useRestoreLocation({
    mutation: {
      onSuccess: async () => {
        await invalidateLocations(queryClient);
        toast.success(t("locationRestored"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}
