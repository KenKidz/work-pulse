"use client";

/**
 * Create/edit dialog for a single position — title + description, RHF + zod,
 * server errors rendered as visible field errors. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import {
  positionFieldErrors,
  usePositionCreate,
  usePositionUpdate,
  type PositionFieldName,
} from "../hooks/use-positions";
import { positionFormSchema, type PositionFormValues } from "../schema";
import type { Position } from "../types";

type PositionFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a position = edit mode. */
  position: Position | null;
};

export function PositionFormDialog({ open, onOpenChange, position }: PositionFormDialogProps) {
  const t = useTranslations();
  const errors = useTranslations("organizations.errors");
  const isEdit = position !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createPosition = usePositionCreate();
  const updatePosition = usePositionUpdate();
  const mutation = isEdit ? updatePosition : createPosition;

  const form = useForm<PositionFormValues>({
    resolver: zodResolver(positionFormSchema((key) => t(`organizations.positions.validation.${key}`))),
    defaultValues: {
      title: position?.title ?? "",
      description: position?.description ?? null,
    },
  });

  async function onSubmit(values: PositionFormValues) {
    setRootError(null);
    const payload = {
      title: values.title,
      description: values.description ?? null,
    };

    try {
      if (position) {
        await updatePosition.mutateAsync({ id: position.id, data: payload });
      } else {
        await createPosition.mutateAsync({ data: payload });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = positionFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as PositionFieldName, { type: "server", message });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("organizations.positions.editTitle") : t("organizations.positions.createTitle")}</DialogTitle>
          <DialogDescription>{t("organizations.positions.description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("organizations.positions.title")}</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder={t("organizations.positions.titlePlaceholder")} autoComplete="off" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("organizations.positions.descriptionField")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value ?? ""}
                      placeholder={t("organizations.positions.descriptionPlaceholder")}
                      rows={3}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {rootError ? (
              <p role="alert" className="text-sm text-destructive">
                {rootError}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("common.actions.cancel")}
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {isEdit ? t("organizations.actions.saveChanges") : t("organizations.actions.createPosition")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
