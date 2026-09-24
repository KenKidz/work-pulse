import Link from "next/link";

export default function HomePage() {
  return (
    <main className="container">
      <h1>WorkPulse</h1>
      <p>Real-time workforce management and people analytics.</p>
      <p className="muted">
        Phase 1 scaffold — the Employees and Attendance features land here next.
      </p>
      <Link className="button" href="/dashboard">
        Open dashboard
      </Link>
    </main>
  );
}