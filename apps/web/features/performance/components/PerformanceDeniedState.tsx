"use client";

/**
 * The quiet denied state shared by the performance surfaces: the API has
 * already decided (403), the UI only explains it and offers no action.
 */

import { ShieldX } from "lucide-react";

import { EmptyState } from "@/components/empty-state";

export function PerformanceDeniedState({ message }: { message: string }) {
  return <EmptyState icon={ShieldX} message={message} />;
}
