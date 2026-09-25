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
import { toast } from "sonner";

import { invalidateLocations } from "../api";
import type { ListLocationsParams } from "../types";
import { errorMessage } from "./use-departments";

export function useLocations(params?: ListLocationsParams) {
  return useListLocations(params);
}

export type LocationFieldName = "name" | "address" | "timezone";
export type LocationFieldErrors = Partial<Record<LocationFieldName | "root", string>>;

/** Map a stable API error code to the field it belongs to. */
export function locationFieldErrors(error: unknown): LocationFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "location_name_taken":
        return { name: "This name is already used by another location." };
      case "invalid_timezone":
        return { timezone: "Enter a valid IANA timezone, e.g. Asia/Ho_Chi_Minh." };
      case "forbidden":
        return { root: "You do not have permission to manage organization data." };
      default:
        return { root: error.message };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function useLocationCreate() {
  const queryClient = useQueryClient();
  return useCreateLocation({
    mutation: {
      onSuccess: async () => {
        await invalidateLocations(queryClient);
        toast.success("Location created");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useLocationUpdate() {
  const queryClient = useQueryClient();
  return useUpdateLocation({
    mutation: {
      onSuccess: async () => {
        await invalidateLocations(queryClient);
        toast.success("Location updated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useLocationArchive() {
  const queryClient = useQueryClient();
  return useArchiveLocation({
    mutation: {
      onSuccess: async () => {
        await invalidateLocations(queryClient);
        toast.success("Location archived");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useLocationRestore() {
  const queryClient = useQueryClient();
  return useRestoreLocation({
    mutation: {
      onSuccess: async () => {
        await invalidateLocations(queryClient);
        toast.success("Location restored");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}
