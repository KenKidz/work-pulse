/**
 * Query-cache keys, invalidation helpers and the presigned storage POST for
 * the documents feature.
 *
 * The generated client keys queries by URL, so invalidating with the
 * parameterless URL prefix refreshes every category/page variation of an
 * employee's list. The storage POST is deliberately a plain `fetch`: it goes
 * to a presigned MinIO/S3 URL, not to the WorkPulse API (ADR 0022).
 */

import type { QueryClient } from "@tanstack/react-query";
import { getListDocumentsQueryKey } from "@workpulse/api-client";

import type { DocumentUpload } from "./types";

export function documentsQueryKey(employeeId: string) {
  return getListDocumentsQueryKey(employeeId);
}

export function invalidateDocuments(queryClient: QueryClient, employeeId: string): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: documentsQueryKey(employeeId) });
}

/**
 * Upload the file straight to private storage through the presigned POST.
 *
 * The signed fields are sent verbatim, the filename travels percent-encoded
 * in `x-amz-meta-filename` (ADR 0022) and the file is appended last. A failed
 * POST carries no body details: the status alone is enough, and the URL/key is
 * a credential that must never be logged.
 */
export async function uploadStagedObject(
  upload: DocumentUpload,
  file: File,
  signal?: AbortSignal,
): Promise<void> {
  const body = new FormData();
  for (const [name, value] of Object.entries(upload.fields)) {
    body.append(name, value);
  }
  body.append("x-amz-meta-filename", encodeURIComponent(file.name));
  body.append("file", file);

  const response = await fetch(upload.url, { method: "POST", body, signal });
  if (!response.ok) {
    throw new Error(`Storage upload failed with status ${response.status}`);
  }
}
