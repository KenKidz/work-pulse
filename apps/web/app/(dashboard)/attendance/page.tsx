import type { Metadata } from "next";

import { AttendanceBoard } from "@/features/attendance/components/AttendanceBoard";

export const metadata: Metadata = {
  title: "Attendance — WorkPulse",
};

export default function AttendancePage() {
  return (
    <section className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Attendance</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The live workforce — check in, watch the day unfold, inspect the records.
        </p>
      </header>
      <AttendanceBoard />
    </section>
  );
}
