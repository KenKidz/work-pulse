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
import { toast } from "sonner";

import { invalidatePositions } from "../api";
import type { ListPositionsParams } from "../types";
import { errorMessage } from "./use-departments";

export function usePositions(params?: ListPositionsParams) {
  return useListPositions(params);
}

export type PositionFieldName = "title" | "description";
export type PositionFieldErrors = Partial<Record<PositionFieldName | "root", string>>;

/** Map a stable API error code to the field it belongs to. */
export function positionFieldErrors(error: unknown): PositionFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "position_title_taken":
        return { title: "This title is already used by another position." };
      case "forbidden":
        return { root: "You do not have permission to manage organization data." };
      default:
        return { root: error.message };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function usePositionCreate() {
  const queryClient = useQueryClient();
  return useCreatePosition({
    mutation: {
      onSuccess: async () => {
        await invalidatePositions(queryClient);
        toast.success("Position created");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function usePositionUpdate() {
  const queryClient = useQueryClient();
  return useUpdatePosition({
    mutation: {
      onSuccess: async () => {
        await invalidatePositions(queryClient);
        toast.success("Position updated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function usePositionArchive() {
  const queryClient = useQueryClient();
  return useArchivePosition({
    mutation: {
      onSuccess: async () => {
        await invalidatePositions(queryClient);
        toast.success("Position archived");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function usePositionRestore() {
  const queryClient = useQueryClient();
  return useRestorePosition({
    mutation: {
      onSuccess: async () => {
        await invalidatePositions(queryClient);
        toast.success("Position restored");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}
