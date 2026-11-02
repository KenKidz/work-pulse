"use client";

/**
 * Create/edit dialog for a review — RHF + zod against the pinned
 * `ReviewCreate`/`ReviewUpdate` shapes. Create picks the subject and nominates
 * peers; edit adds the evaluative fields (rating 1–5, strengths, improvements)
 * and churns the nomination set while the review is still a draft. A failed
 * save keeps the dialog and the user's input, with the mapped server sentence
 * on the field that caused it. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { useApiErrorTranslator } from "@/lib/api-errors";

import {
  reviewFieldErrors,
  usePerformanceAccess,
  usePerformanceDirectory,
  useReviewCreate,
  useReviewUpdate,
  type ReviewFieldName,
} from "../hooks/use-performance";
import { employeeLabel } from "../labels";
import {
  MAX_REVIEW_PROVIDERS,
  NO_RATING,
  reviewFormSchema,
  REVIEW_RATINGS,
  type ReviewFormValues,
} from "../schema";
import type { Review } from "../types";

const NO_RATING_VALUE = "none";

type ReviewFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; a draft review = edit. */
  review: Review | null;
};

export function ReviewFormDialog({ open, onOpenChange, review }: ReviewFormDialogProps) {
  const t = useTranslations("performance.reviews.form");
  const common = useTranslations("performance.actions");
  const validation = useTranslations("performance.validation");
  const errors = useApiErrorTranslator("performance.errors");
  const fallbacks = useTranslations("performance.fallbacks");
  const ratings = useTranslations("performance.reviews.ratings");
  const { user, canViewTeam } = usePerformanceAccess();
  const directory = usePerformanceDirectory();
  const isEdit = review !== null;
  const canChooseSubject = canViewTeam && !isEdit;
  const hasSelfLink = user.employee_id != null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createReview = useReviewCreate();
  const updateReview = useReviewUpdate();
  const mutation = isEdit ? updateReview : createReview;

  const form = useForm<ReviewFormValues>({
    resolver: zodResolver(reviewFormSchema((key) => validation(key))),
    defaultValues: {
      employee_id: review?.employee_id ?? "",
      period: review?.period ?? "",
      rating: review?.rating != null ? String(review.rating) : NO_RATING,
      strengths: review?.strengths ?? "",
      improvements: review?.improvements ?? "",
      feedback_provider_ids: review?.feedback_provider_ids ?? [],
    },
  });

  const subjectId = useWatch({ control: form.control, name: "employee_id" });
  const selectedProviderIds = useWatch({
    control: form.control,
    name: "feedback_provider_ids",
  });

  const subjectOptions = directory.pickerEmployees.filter(
    (employee) => !hasSelfLink || employee.id !== user.employee_id,
  );
  const reviewedName =
    review === null
      ? ""
      : employeeLabel(
          directory.nameById,
          review.employee_id,
          fallbacks("employee", { id: review.employee_id.slice(-4) }),
        );

  // Active, in-scope choices; a nomination that dropped out of the directory
  // stays visible (with the localized id fallback) so the author can remove it.
  const activeProviderChoices = directory.pickerEmployees
    .filter(
      (employee) =>
        (!hasSelfLink || employee.id !== user.employee_id) &&
        (subjectId === "" || employee.id !== subjectId),
    )
    .map((employee) => ({
      id: employee.id,
      label: `${employee.first_name} ${employee.last_name}`.trim(),
    }));
  const providerChoices = [
    ...activeProviderChoices,
    ...selectedProviderIds
      .filter((id) => !activeProviderChoices.some((choice) => choice.id === id))
      .map((id) => ({
        id,
        label: employeeLabel(directory.nameById, id, fallbacks("employee", { id: id.slice(-4) })),
      })),
  ];

  async function onSubmit(values: ReviewFormValues) {
    setRootError(null);

    if (review === null) {
      try {
        await createReview.mutateAsync({
          data: {
            employee_id: values.employee_id,
            period: values.period.trim() === "" ? null : values.period.trim(),
            feedback_provider_ids: values.feedback_provider_ids,
          },
        });
        onOpenChange(false);
      } catch (error) {
        const fieldErrors = reviewFieldErrors(error, errors, {
          hasEmployeeField: canChooseSubject,
          hasProviderField: true,
        });
        for (const [field, message] of Object.entries(fieldErrors)) {
          if (field === "root") {
            setRootError(message ?? null);
          } else {
            form.setError(field as Exclude<ReviewFieldName, "root">, {
              type: "server",
              message,
            });
          }
        }
      }
      return;
    }

    try {
      await updateReview.mutateAsync({
        id: review.id,
        data: {
          period: values.period.trim() === "" ? null : values.period.trim(),
          rating: values.rating === NO_RATING ? null : Number(values.rating),
          strengths: values.strengths.trim() === "" ? null : values.strengths.trim(),
          improvements: values.improvements.trim() === "" ? null : values.improvements.trim(),
          feedback_provider_ids: values.feedback_provider_ids,
        },
      });
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = reviewFieldErrors(error, errors, {
        hasEmployeeField: false,
        hasProviderField: true,
      });
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root") {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<ReviewFieldName, "root">, {
            type: "server",
            message,
          });
        }
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("editTitle") : t("createTitle")}</DialogTitle>
          <DialogDescription>
            {isEdit ? t("editDescription") : t("createDescription")}
          </DialogDescription>
          {isEdit ? (
            <p className="text-sm text-muted-foreground">
              {t("editingFor", { name: reviewedName })}
            </p>
          ) : null}
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {canChooseSubject ? (
              <FormField
                control={form.control}
                name="employee_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel hint={t("employeeHint")}>{t("employee")}</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={(value) => {
                        // The subject can never be their own peer provider.
                        field.onChange(value);
                        form.setValue(
                          "feedback_provider_ids",
                          form
                            .getValues("feedback_provider_ids")
                            .filter((providerId) => providerId !== value),
                        );
                      }}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={t("selectEmployee")} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {subjectOptions.map((employee) => (
                          <SelectItem key={employee.id} value={employee.id}>
                            {`${employee.first_name} ${employee.last_name}`.trim()}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}

            <FormField
              control={form.control}
              name="period"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("periodHint")}>{t("period")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      value={field.value}
                      placeholder={t("periodPlaceholder")}
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {isEdit ? (
              <>
                <FormField
                  control={form.control}
                  name="rating"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint={t("ratingHint")}>{t("rating")}</FormLabel>
                      <Select
                        value={field.value === NO_RATING ? NO_RATING_VALUE : field.value}
                        onValueChange={(value) =>
                          field.onChange(value === NO_RATING_VALUE ? NO_RATING : value)
                        }
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue placeholder={t("chooseRating")} />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          <SelectItem value={NO_RATING_VALUE}>{t("noRating")}</SelectItem>
                          {REVIEW_RATINGS.map((value) => (
                            <SelectItem key={value} value={value}>
                              {ratings("option", { rating: value })}
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
                  name="strengths"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint={t("strengthsHint")}>{t("strengths")}</FormLabel>
                      <FormControl>
                        <Textarea
                          {...field}
                          value={field.value}
                          rows={3}
                          placeholder={t("strengthsPlaceholder")}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="improvements"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel hint={t("improvementsHint")}>{t("improvements")}</FormLabel>
                      <FormControl>
                        <Textarea
                          {...field}
                          value={field.value}
                          rows={3}
                          placeholder={t("improvementsPlaceholder")}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </>
            ) : null}

            <FormField
              control={form.control}
              name="feedback_provider_ids"
              render={({ field }) => (
                <FormItem>
                  <FormLabel hint={t("providersHint")}>{t("providers")}</FormLabel>
                  <FormControl>
                    <div role="group" aria-label={t("providers")} className="flex flex-wrap gap-2">
                      {providerChoices.length === 0 ? (
                        <p className="text-sm text-muted-foreground">{t("providersEmpty")}</p>
                      ) : (
                        providerChoices.map((choice) => {
                          const selected = field.value.includes(choice.id);
                          return (
                            <Button
                              key={choice.id}
                              type="button"
                              size="sm"
                              className="min-h-11 sm:min-h-8"
                              variant={selected ? "secondary" : "outline"}
                              aria-pressed={selected}
                              disabled={!selected && field.value.length >= MAX_REVIEW_PROVIDERS}
                              onClick={() =>
                                field.onChange(
                                  selected
                                    ? field.value.filter((providerId) => providerId !== choice.id)
                                    : [...field.value, choice.id],
                                )
                              }
                            >
                              {choice.label}
                            </Button>
                          );
                        })
                      )}
                    </div>
                  </FormControl>
                  <p className="text-xs text-muted-foreground">
                    {t("providersCount", {
                      selected: field.value.length,
                      max: MAX_REVIEW_PROVIDERS,
                    })}
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />

            {rootError ? (
              <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {rootError}
              </p>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {common("cancel")}
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending
                  ? isEdit
                    ? t("saving")
                    : t("creating")
                  : isEdit
                    ? t("save")
                    : t("create")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
