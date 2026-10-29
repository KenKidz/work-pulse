"use client";

/**
 * The metadata field group shared by the upload and edit dialogs: title,
 * category and optional expiry. Both forms use `documentMetadataSchema`, so
 * one component keeps the markup, options and hints from drifting.
 */

import { useTranslations } from "next-intl";
import { useFormContext } from "react-hook-form";

import { FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { DOCUMENT_CATEGORIES, type DocumentMetadataValues } from "../schema";

type DocumentMetadataFieldsProps = {
  /** Upload-only placeholder; the edit form always has a value. */
  titlePlaceholder?: string;
};

export function DocumentMetadataFields({ titlePlaceholder }: DocumentMetadataFieldsProps) {
  const form = useFormContext<DocumentMetadataValues>();
  const t = useTranslations("documents.metadata");
  const categories = useTranslations("documents.categories");

  return (
    <>
      <FormField
        control={form.control}
        name="title"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("title")}</FormLabel>
            <FormControl>
              <Input {...field} placeholder={titlePlaceholder} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />

      <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
        <FormField
          control={form.control}
          name="category"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("category")}</FormLabel>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger aria-label={t("category")}>
                    <SelectValue placeholder={t("selectCategory")} />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {DOCUMENT_CATEGORIES.map((category) => (
                    <SelectItem key={category} value={category}>
                      {categories(category)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="expiry_date"
          render={({ field }) => (
            <FormItem>
              <FormLabel hint={t("expiryHint")}>{t("expiry")}</FormLabel>
              <FormControl>
                <Input {...field} value={field.value} type="date" />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>
    </>
  );
}
