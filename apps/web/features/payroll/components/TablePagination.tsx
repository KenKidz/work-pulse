"use client";

/**
 * Shared pagination footer for payroll tables. The house clamp shape means
 * `page` metadata is always present; the control only renders when there is a
 * second page, and both buttons carry the table-specific accessible name.
 */

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import type { Page } from "../types";

type TablePaginationProps = {
  page: Page;
  /** Human table name used in the controls' accessible labels. */
  label: string;
  onPageChange: (page: number) => void;
};

export function TablePagination({ page, label, onPageChange }: TablePaginationProps) {
  const t = useTranslations("payroll.pagination");

  if (page.total_pages <= 1) return null;

  return (
    <div className="flex items-center justify-end gap-2 border-t px-4 py-3 text-sm">
      <Button
        variant="outline"
        size="sm"
        className="min-h-11 sm:min-h-8"
        aria-label={t("previousAria", { label })}
        disabled={page.page <= 1}
        onClick={() => onPageChange(page.page - 1)}
      >
        {t("previous")}
      </Button>
      <span className="text-muted-foreground">
        {t("pageOf", { page: page.page, total: page.total_pages })}
      </span>
      <Button
        variant="outline"
        size="sm"
        className="min-h-11 sm:min-h-8"
        aria-label={t("nextAria", { label })}
        disabled={page.page >= page.total_pages}
        onClick={() => onPageChange(page.page + 1)}
      >
        {t("next")}
      </Button>
    </div>
  );
}
