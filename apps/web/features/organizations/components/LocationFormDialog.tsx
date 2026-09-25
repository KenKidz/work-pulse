"use client";

/**
 * Create/edit dialog for a single location — name, address, timezone (IANA,
 * validated server-side; `invalid_timezone` maps to a timezone field error).
 * Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
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
  const isEdit = location !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createLocation = useLocationCreate();
  const updateLocation = useLocationUpdate();
  const mutation = isEdit ? updateLocation : createLocation;

  const form = useForm<LocationFormValues>({
    resolver: zodResolver(locationFormSchema),
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
      const fieldErrors = locationFieldErrors(error);
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
          <DialogTitle>{isEdit ? "Edit location" : "New location"}</DialogTitle>
          <DialogDescription>
            Attendance lateness is evaluated in the location&apos;s timezone.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder="Ho Chi Minh Office" autoComplete="off" />
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
                  <FormLabel>Address</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value ?? ""}
                      placeholder="Street, city, country"
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
                  <FormLabel>Timezone</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder="Asia/Ho_Chi_Minh" autoComplete="off" />
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
                Cancel
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {isEdit ? "Save changes" : "Create location"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
