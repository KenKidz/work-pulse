import type { Metadata } from "next";

import { AttendanceBoard } from "@/features/attendance/components/AttendanceBoard";

export const metadata: Metadata = {
  title: "Attendance — WorkPulse",
};

export default function AttendancePage() {
  return (
    <section className="space-y-6">
      <AttendanceBoard />
    </section>
  );
}
