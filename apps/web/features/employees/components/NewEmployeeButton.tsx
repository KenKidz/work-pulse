"use client";

/**
 * Page-level create action for the employee directory. Kept as its own client
 * leaf so the employees page stays a Server Component and the create dialog
 * is not nested inside the table surface. Client leaf.
 */

import { UserPlus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";

import { EmployeeFormDialog } from "./EmployeeFormDialog";

export function NewEmployeeButton() {
  const t = useTranslations("employees");
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <UserPlus aria-hidden="true" />
        {t("table.newEmployee")}
      </Button>
      <EmployeeFormDialog open={open} onOpenChange={setOpen} employee={null} />
    </>
  );
}
