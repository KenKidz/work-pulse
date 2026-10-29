"use client";

/**
 * One row in the Documents table: title and original filename, category,
 * expiry (with an expired badge), upload time and the row actions. Expiry is
 * display-only in v1 — a past date never blocks anything. Client leaf.
 */

import { Download, Pencil, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TableCell, TableRow } from "@/components/ui/table";

import { formatDocumentDate, formatDocumentTimestamp, isExpiredDate } from "../dates";
import type { Document } from "../types";

type DocumentRowProps = {
  document: Document;
  canWrite: boolean;
  onDownload: (documentId: string) => void;
  onEdit: (document: Document) => void;
  onDelete: (document: Document) => void;
};

export function DocumentRow({
  document,
  canWrite,
  onDownload,
  onEdit,
  onDelete,
}: DocumentRowProps) {
  const t = useTranslations("documents");
  const locale = useLocale();
  const expired = isExpiredDate(document.expiry_date);

  return (
    <TableRow>
      <TableCell className="max-w-64 pl-3">
        <p className="truncate font-medium" title={document.title}>
          {document.title}
        </p>
        <p className="truncate text-xs text-muted-foreground">{document.filename}</p>
      </TableCell>
      <TableCell>
        <Badge variant="outline">{t(`categories.${document.category}`)}</Badge>
      </TableCell>
      <TableCell>
        {document.expiry_date ? (
          <span className="inline-flex items-center gap-2">
            <span className={expired ? "text-destructive" : undefined}>
              {formatDocumentDate(document.expiry_date, locale)}
            </span>
            {expired ? <Badge variant="destructive">{t("expired")}</Badge> : null}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {formatDocumentTimestamp(document.created_at, locale)}
      </TableCell>
      <TableCell className="pr-3">
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-11 sm:size-8"
            aria-label={t("actions.download", { title: document.title })}
            onClick={() => onDownload(document.id)}
          >
            <Download aria-hidden="true" />
          </Button>
          {canWrite ? (
            <>
              <Button
                variant="ghost"
                size="icon"
                className="size-11 sm:size-8"
                aria-label={t("actions.edit", { title: document.title })}
                onClick={() => onEdit(document)}
              >
                <Pencil aria-hidden="true" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-11 sm:size-8"
                aria-label={t("actions.delete", { title: document.title })}
                onClick={() => onDelete(document)}
              >
                <Trash2 aria-hidden="true" />
              </Button>
            </>
          ) : null}
        </div>
      </TableCell>
    </TableRow>
  );
}
