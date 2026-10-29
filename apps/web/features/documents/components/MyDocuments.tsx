"use client";

/**
 * "My documents" — the own-profile surface's read-only document list.
 *
 * The current employee is resolved through `/employees/me`; the list is scoped
 * to that id and the panel renders with `readOnly` for every role, so no
 * upload/edit/delete control ever appears here even for HR. An account with no
 * employee link (HR/Admin/Finance in the dev seed) gets the quiet not-linked
 * state instead of an error. Client leaf.
 */

import { CircleAlert, FileText } from "lucide-react";
import { useTranslations } from "next-intl";
import { useGetCurrentEmployee } from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";

import { DocumentsPanel, DocumentsTableSkeleton } from "./DocumentsPanel";

function LoadError({
  message,
  retryLabel,
  onRetry,
}: {
  message: string;
  retryLabel: string;
  onRetry: () => void;
}) {
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertDescription>
        <p>{message}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          {retryLabel}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

export function MyDocuments() {
  const t = useTranslations("documents.myDocuments");
  const common = useTranslations("common.actions");
  const me = useGetCurrentEmployee();
  const employee = me.data?.status === 200 ? me.data.data : null;

  if (me.isPending) {
    return (
      <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
        <DocumentsTableSkeleton label={t("loading")} rows={3} />
      </Card>
    );
  }

  if (me.isError) {
    if (me.error instanceof ApiError && me.error.code === "employee_not_found") {
      return <EmptyState icon={FileText} message={t("noEmployee")} />;
    }
    return (
      <LoadError
        message={t("loadError")}
        retryLabel={common("retry")}
        onRetry={() => void me.refetch()}
      />
    );
  }

  if (employee === null) {
    return (
      <LoadError
        message={t("loadError")}
        retryLabel={common("retry")}
        onRetry={() => void me.refetch()}
      />
    );
  }

  return <DocumentsPanel employeeId={employee.id} readOnly />;
}
