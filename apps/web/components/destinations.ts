import { Building2, CalendarClock, LayoutDashboard, Users, type LucideIcon } from "lucide-react";

export type Destination = {
  href: string;
  /** Short label for navigation. */
  label: string;
  icon: LucideIcon;
  /** Landing-page blurb. */
  blurb: string;
  /** Dashboard one-line summary. */
  summary: string;
};

/**
 * The Phase 1 surfaces — single source of truth for the primary navigation,
 * the landing cards and the dashboard cards, so routes, labels and icons
 * cannot drift between them.
 */
export const DESTINATIONS: Destination[] = [
  {
    href: "/employees",
    label: "Employees",
    icon: Users,
    blurb: "The workforce record — who works where, in what role, since when.",
    summary: "Create, transfer and terminate — the append-only employment record.",
  },
  {
    href: "/attendance",
    label: "Attendance",
    icon: CalendarClock,
    blurb: "Check in, watch the day unfold, inspect the auditable record.",
    summary: "The live day board: counts, recent activity and the auditable records.",
  },
  {
    href: "/settings/organization",
    label: "Organization",
    icon: Building2,
    blurb: "Departments, positions and locations your workforce hangs off.",
    summary: "Departments, positions and locations — with archive and restore.",
  },
];

/**
 * Every primary navigation destination — Dashboard plus the Phase 1
 * surfaces — so the desktop nav and the mobile drawer cannot drift.
 */
export const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  ...DESTINATIONS.map(({ href, label, icon }) => ({ href, label, icon })),
];
