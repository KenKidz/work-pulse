"use client";

/**
 * Create/edit dialog for a single location — name, address, timezone (IANA,
 * validated server-side; `invalid_timezone` maps to a timezone field error).
 * Client leaf.
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

import { useApiErrorTranslator } from "@/lib/api-errors";

import {
  locationFieldErrors,
  useLocationCreate,
  useLocationUpdate,
  type LocationFieldName,
} from "../hooks/use-locations";
import { locationFormSchema, type LocationFormValues } from "../schema";
import type { Location } from "../types";

type LocationFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a location = edit mode. */
  location: Location | null;
};

export function LocationFormDialog({ open, onOpenChange, location }: LocationFormDialogProps) {
  const t = useTranslations();
  const errors = useApiErrorTranslator("organizations.errors");
  const isEdit = location !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createLocation = useLocationCreate();
  const updateLocation = useLocationUpdate();
  const mutation = isEdit ? updateLocation : createLocation;

  const form = useForm<LocationFormValues>({
    resolver: zodResolver(
      locationFormSchema((key) => t(`organizations.locations.validation.${key}`)),
    ),
    defaultValues: {
      name: location?.name ?? "",
      address: location?.address ?? null,
      timezone: location?.timezone ?? "",
    },
  });

  async function onSubmit(values: LocationFormValues) {
    setRootError(null);
    const payload = {
      name: values.name,
      address: values.address ?? null,
      timezone: values.timezone,
    };

    try {
      if (location) {
        await updateLocation.mutateAsync({ id: location.id, data: payload });
      } else {
        await createLocation.mutateAsync({ data: payload });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = locationFieldErrors(error, errors);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as LocationFieldName, { type: "server", message });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isEdit
              ? t("organizations.locations.editTitle")
              : t("organizations.locations.createTitle")}
          </DialogTitle>
          <DialogDescription>{t("organizations.locations.description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("organizations.locations.name")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder={t("organizations.locations.namePlaceholder")}
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="address"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("organizations.locations.address")}</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value ?? ""}
                      placeholder={t("organizations.locations.addressPlaceholder")}
                      rows={2}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="timezone"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("organizations.locations.timezone")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder={t("organizations.locations.timezonePlaceholder")}
                      autoComplete="off"
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
                {isEdit
                  ? t("organizations.actions.saveChanges")
                  : t("organizations.actions.createLocation")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
