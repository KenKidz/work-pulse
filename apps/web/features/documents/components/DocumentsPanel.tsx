"use client";

/**
 * The employee's documents panel — list, category filter, upload/edit/delete
 * for HR/Admin/Super Admin and download for every authorized reader. The API
 * owns read scope (owner, team manager, HR/Admin); a 403 becomes the quiet
 * denied state. Client leaf.
 */

import { CircleAlert, FileText, ShieldX, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuth } from "@/features/auth/components/AuthGate";

import { useDocumentDownload, useDocuments } from "../hooks/use-documents";
import { isForbiddenError } from "../labels";
import { canManageDocuments } from "../roles";
import { DOCUMENT_CATEGORIES } from "../schema";
import type { Document, DocumentCategory } from "../types";
import { DeleteDocumentDialog } from "./DeleteDocumentDialog";
import { DocumentRow } from "./DocumentRow";
import { EditDocumentDialog } from "./EditDocumentDialog";
import { UploadDocumentDialog } from "./UploadDocumentDialog";

const DOCUMENTS_PAGE_SIZE = 20;
const ALL_CATEGORIES = "all";
const SKELETON_ROWS = 5;

function DocumentsTableSkeleton({ label }: { label: string }) {
  return (
    <div role="status" className="w-full">
      <span className="sr-only">{label}</span>
      <div aria-hidden="true">
        <div className="flex h-10 items-center gap-4 border-b bg-muted/50 px-3">
          <span className="h-3 w-1/3 rounded-sm bg-muted" />
          <span className="h-3 w-20 rounded-sm bg-muted" />
          <span className="h-3 w-24 rounded-sm bg-muted" />
          <span className="h-3 w-24 rounded-sm bg-muted" />
          <span className="ml-auto h-3 w-16 rounded-sm bg-muted" />
        </div>
        {Array.from({ length: SKELETON_ROWS }).map((_, index) => (
          <div key={index} className="flex h-12 items-center gap-4 border-b px-3 last:border-b-0">
            <span className="h-3 w-1/3 rounded-sm bg-muted" />
            <span className="h-3 w-20 rounded-sm bg-muted" />
            <span className="h-3 w-24 rounded-sm bg-muted" />
            <span className="h-3 w-24 rounded-sm bg-muted" />
            <span className="ml-auto h-3 w-16 rounded-sm bg-muted" />
          </div>
        ))}
      </div>
    </div>
  );
}

type DocumentsPanelProps = {
  employeeId: string;
  /** T7's own-profile surface renders the same panel with no write controls. */
  readOnly?: boolean;
};

export function DocumentsPanel({ employeeId, readOnly = false }: DocumentsPanelProps) {
  const t = useTranslations("documents");
  const categories = useTranslations("documents.categories");
  const pagination = useTranslations("documents.pagination");
  const common = useTranslations("common.actions");
  const { user } = useAuth();
  const canWrite = !readOnly && canManageDocuments(user.role);
  const [page, setPage] = useState(1);
  const [category, setCategory] = useState<DocumentCategory | typeof ALL_CATEGORIES>(
    ALL_CATEGORIES,
  );
  const [uploadOpen, setUploadOpen] = useState(false);
  const [editing, setEditing] = useState<Document | null>(null);
  const [deleting, setDeleting] = useState<Document | null>(null);
  const download = useDocumentDownload(employeeId);

  const documentsQuery = useDocuments(employeeId, {
    page,
    page_size: DOCUMENTS_PAGE_SIZE,
    category: category === ALL_CATEGORIES ? undefined : category,
  });
  const documents = documentsQuery.data?.status === 200 ? documentsQuery.data.data.items : [];
  const pageInfo = documentsQuery.data?.status === 200 ? documentsQuery.data.data.page : null;
  const filtersActive = category !== ALL_CATEGORIES;
  // The API echoes the requested page; a delete can leave the local page beyond
  // the new last page. Offer an explicit way back instead of a dead end.
  const pageOutOfRange = pageInfo !== null && pageInfo.page > Math.max(pageInfo.total_pages, 1);

  function changeCategory(value: string) {
    setCategory(value as DocumentCategory | typeof ALL_CATEGORIES);
    setPage(1);
  }

  return (
    <section aria-labelledby="documents-panel-title" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="documents-panel-title" className="text-lg font-semibold tracking-tight">
            {t("title")}
          </h2>
          <p className="max-w-2xl text-sm text-pretty text-muted-foreground">{t("description")}</p>
        </div>
        {canWrite ? (
          <Button onClick={() => setUploadOpen(true)}>
            <Upload aria-hidden="true" />
            {t("actions.upload")}
          </Button>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={category} onValueChange={changeCategory}>
          <SelectTrigger className="w-full sm:w-56" aria-label={t("filters.categoryAria")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_CATEGORIES}>{t("filters.allCategories")}</SelectItem>
            {DOCUMENT_CATEGORIES.map((option) => (
              <SelectItem key={option} value={option}>
                {categories(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Card
        aria-busy={documentsQuery.isPlaceholderData}
        className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none"
      >
        {documentsQuery.isPending ? (
          <DocumentsTableSkeleton label={t("loading")} />
        ) : documentsQuery.isError ? (
          isForbiddenError(documentsQuery.error) ? (
            <div className="p-4">
              <EmptyState icon={ShieldX} message={t("denied")} />
            </div>
          ) : (
            <div className="p-4">
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>
                  <p>{t("loadError")}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void documentsQuery.refetch()}
                  >
                    {common("retry")}
                  </Button>
                </AlertDescription>
              </Alert>
            </div>
          )
        ) : documents.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={FileText}
              message={
                pageOutOfRange ? t("emptyPage") : filtersActive ? t("emptyFiltered") : t("empty")
              }
            />
            {pageOutOfRange ? (
              <div className="mt-3 flex justify-center">
                <Button
                  variant="outline"
                  size="sm"
                  className="min-h-11 sm:min-h-8"
                  onClick={() => setPage(1)}
                >
                  {t("backToFirstPage")}
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">{t("swipe")}</p>
            <Table aria-label={t("label")} containerLabel={t("containerLabel")}>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-3">{t("columns.title")}</TableHead>
                  <TableHead>{t("columns.category")}</TableHead>
                  <TableHead>{t("columns.expiry")}</TableHead>
                  <TableHead>{t("columns.uploaded")}</TableHead>
                  <TableHead className="pr-3 text-right">{t("columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((document) => (
                  <DocumentRow
                    key={document.id}
                    document={document}
                    canWrite={canWrite}
                    onDownload={(documentId) => void download(documentId)}
                    onEdit={setEditing}
                    onDelete={setDeleting}
                  />
                ))}
              </TableBody>
            </Table>

            {pageInfo !== null && pageInfo.total_pages > 1 ? (
              <div className="flex items-center justify-end gap-2 border-t px-4 py-3 text-sm">
                <Button
                  variant="outline"
                  size="sm"
                  className="min-h-11 sm:min-h-8"
                  aria-label={pagination("previousAria", { label: t("label") })}
                  disabled={pageInfo.page <= 1}
                  onClick={() => setPage(pageInfo.page - 1)}
                >
                  {pagination("previous")}
                </Button>
                <span className="text-muted-foreground">
                  {pagination("pageOf", { page: pageInfo.page, total: pageInfo.total_pages })}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  className="min-h-11 sm:min-h-8"
                  aria-label={pagination("nextAria", { label: t("label") })}
                  disabled={pageInfo.page >= pageInfo.total_pages}
                  onClick={() => setPage(pageInfo.page + 1)}
                >
                  {pagination("next")}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Card>

      {canWrite ? (
        <>
          <UploadDocumentDialog
            open={uploadOpen}
            onOpenChange={setUploadOpen}
            employeeId={employeeId}
          />
          {editing !== null ? (
            <EditDocumentDialog
              open
              onOpenChange={(open) => {
                if (!open) setEditing(null);
              }}
              employeeId={employeeId}
              document={editing}
            />
          ) : null}
          {deleting !== null ? (
            <DeleteDocumentDialog
              open
              onOpenChange={(open) => {
                if (!open) setDeleting(null);
              }}
              employeeId={employeeId}
              document={deleting}
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}
