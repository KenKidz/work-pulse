"use client";

/**
 * Create/edit dialog for a candidate — HR-entered intake on an open job, or
 * a contact-field edit. RHF + zod mirror the pinned `CandidateCreate` /
 * `CandidateUpdate` bounds; the server stays the authority and its codes map
 * back to the field that caused them. Client leaf.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert } from "lucide-react";
import { useEffect, useState } from "react";
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
  FormDescription,
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

import {
  candidateFieldErrors,
  useCandidateCreate,
  useCandidateUpdate,
  type CandidateFieldName,
} from "../hooks/use-recruitment";
import { candidateCreateSchema, type CandidateCreateValues } from "../schema";
import type { Candidate, Job } from "../types";

type CandidateFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = intake (create) mode; a candidate = contact edit. */
  candidate: Candidate | null;
  /** Jobs eligible for intake (`open`) — create mode only. */
  openJobs: Job[];
  /** The job currently filtered in the pipeline, preselected when open. */
  initialJobId?: string;
  /** Human job title for the read-only line in edit mode. */
  jobTitle?: string;
  /** The job directory is still loading (create mode). */
  jobsPending?: boolean;
};

export function CandidateFormDialog({
  open,
  onOpenChange,
  candidate,
  openJobs,
  initialJobId,
  jobTitle,
  jobsPending = false,
}: CandidateFormDialogProps) {
  const isEdit = candidate !== null;
  const [rootError, setRootError] = useState<string | null>(null);
  const createCandidate = useCandidateCreate();
  const updateCandidate = useCandidateUpdate();
  const mutation = isEdit ? updateCandidate : createCandidate;

  const defaultJobId =
    initialJobId !== undefined && openJobs.some((job) => job.id === initialJobId)
      ? initialJobId
      : openJobs.length === 1
        ? openJobs[0]?.id
        : "";

  // One schema for both modes: edit keeps the candidate's existing job id in
  // the hidden form state (it is a valid uuid and never submitted), so the
  // shared intake shape validates without a resolver cast.
  const form = useForm<CandidateCreateValues>({
    resolver: zodResolver(candidateCreateSchema),
    defaultValues: {
      job_id: candidate?.job_id ?? defaultJobId ?? "",
      first_name: candidate?.first_name ?? "",
      last_name: candidate?.last_name ?? "",
      email: candidate?.email ?? "",
      phone: candidate?.phone ?? "",
      source: candidate?.source ?? "referral",
      notes: candidate?.notes ?? "",
    },
  });

  // The directory can finish loading after the dialog opened; once exactly one
  // open job is known, preselect it instead of leaving intake blocked on a
  // select the user has no real choice about.
  useEffect(() => {
    if (isEdit || openJobs.length !== 1) return;
    const jobId = openJobs[0]?.id;
    if (jobId !== undefined && form.getValues("job_id") === "") {
      form.setValue("job_id", jobId);
    }
  }, [form, isEdit, openJobs]);

  async function onSubmit(values: CandidateCreateValues) {
    setRootError(null);
    const contact = {
      first_name: values.first_name,
      last_name: values.last_name,
      email: values.email,
      phone: values.phone === "" ? null : values.phone,
      source: values.source,
      notes: values.notes === "" ? null : values.notes,
    };

    try {
      if (candidate === null) {
        await createCandidate.mutateAsync({ jobId: values.job_id, data: contact });
      } else {
        await updateCandidate.mutateAsync({ candidateId: candidate.id, data: contact });
      }
      onOpenChange(false);
    } catch (error) {
      const fieldErrors = candidateFieldErrors(error);
      for (const [field, message] of Object.entries(fieldErrors)) {
        if (field === "root" || (field === "job_id" && isEdit)) {
          setRootError(message ?? null);
        } else {
          form.setError(field as Exclude<CandidateFieldName, "root">, {
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
          <DialogTitle>{isEdit ? "Edit candidate" : "New candidate"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the candidate's contact details. Stage moves and hiring are separate actions."
              : "Add an application to an open job. Intake is HR-entered — candidates never sign in to WorkPulse."}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {isEdit ? (
              <div className="space-y-2">
                <p className="text-sm font-medium">Job</p>
                <p className="text-sm text-muted-foreground">{jobTitle ?? "This job"}</p>
              </div>
            ) : (
              <FormField
                control={form.control}
                name="job_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Job</FormLabel>
                    {openJobs.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {jobsPending
                          ? "Loading open jobs…"
                          : "No job is open. Open a job first — candidates can only be added to an open job."}
                      </p>
                    ) : (
                      <>
                        <Select value={field.value} onValueChange={field.onChange}>
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select an open job" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {openJobs.map((job) => (
                              <SelectItem key={job.id} value={job.id}>
                                {job.title}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormDescription>Only open jobs accept new candidates.</FormDescription>
                      </>
                    )}
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="first_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>First name</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="last_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Last name</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} type="email" autoComplete="off" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="phone"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Phone (optional)</FormLabel>
                    <FormControl>
                      <Input {...field} value={field.value} type="tel" autoComplete="off" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="source"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Source</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="How did they reach us?" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="referral">Referral</SelectItem>
                      <SelectItem value="job_board">Job board</SelectItem>
                      <SelectItem value="agency">Agency</SelectItem>
                      <SelectItem value="other">Other</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="notes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Notes (optional)</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      value={field.value}
                      rows={4}
                      placeholder="Context for the hiring team — never shared with the candidate."
                    />
                  </FormControl>
                  <FormDescription>
                    Sensitive: visible to HR only and never included in events.
                  </FormDescription>
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
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={mutation.isPending || (!isEdit && (openJobs.length === 0 || jobsPending))}
              >
                {mutation.isPending
                  ? isEdit
                    ? "Saving…"
                    : "Adding…"
                  : isEdit
                    ? "Save changes"
                    : "Add candidate"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
