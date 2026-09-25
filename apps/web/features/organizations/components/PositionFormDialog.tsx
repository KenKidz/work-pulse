"use client";

/**
 * Create/edit dialog for a single position — title + description, RHF + zod,
 * server errors rendered as visible field errors. Client leaf.
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
  const isEdit = position !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createPosition = usePositionCreate();
  const updatePosition = usePositionUpdate();
  const mutation = isEdit ? updatePosition : createPosition;

  const form = useForm<PositionFormValues>({
    resolver: zodResolver(positionFormSchema),
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
      const fieldErrors = positionFieldErrors(error);
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
          <DialogTitle>{isEdit ? "Edit position" : "New position"}</DialogTitle>
          <DialogDescription>
            Positions are tenant-global — the same title can span departments.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Title</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder="Senior Nurse" autoComplete="off" />
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
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value ?? ""}
                      placeholder="What this position does"
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
                Cancel
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {isEdit ? "Save changes" : "Create position"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
