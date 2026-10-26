import {
  Banknote,
  BriefcaseBusiness,
  Building2,
  CalendarClock,
  CalendarDays,
  ChartColumn,
  LayoutDashboard,
  MonitorDot,
  ReceiptText,
  Timer,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";

import type { Role } from "@workpulse/api-client/index.schemas";

import { PAYROLL_VIEW_ROLES } from "@/features/payroll/roles";
import { RECRUITMENT_NAV_ROLES } from "@/features/recruitment/roles";
import { REPORT_READ_ROLES } from "@/features/reports/roles";

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

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Restricted to these roles when present; everyone sees the rest. */
  roles?: Role[];
};

/**
 * The roles that manage user accounts — the single web-side source (mirrors
 * the API's `ADMIN_ROLES`): the nav entry hides Users for everyone else and
 * the users page guard redirects non-admins, so the two cannot drift.
 */
export const MANAGE_USER_ROLES: Role[] = ["admin", "super_admin"];

/**
 * The roles that can watch the live command center — the single web-side
 * source (mirrors the API's board policy): the nav entry hides it for
 * everyone else and the page guard redirects, so the two cannot drift.
 */
export const COMMAND_CENTER_ROLES: Role[] = ["manager", "hr", "admin", "super_admin"];

/**
 * The nav entry whose href is the most specific match for the current path.
 * Longest-prefix wins so a nested route (`/payroll/my-payslips/{id}`) marks
 * only its own destination, not the workspace parent.
 */
export function activeNavHref(pathname: string, items: NavItem[]): string | null {
  let active: string | null = null;
  for (const item of items) {
    const matches = pathname === item.href || pathname.startsWith(`${item.href}/`);
    if (matches && (active === null || item.href.length > active.length)) {
      active = item.href;
    }
  }
  return active;
}

/**
 * Every primary navigation destination — Dashboard, the Phase 1 surfaces,
 * Leave, Overtime, the payroll workspace and its every-role "My payslips"
 * sibling, the live Command center and the admin-only Users management — so
 * the desktop nav and the mobile drawer cannot drift. Role-restricted entries
 * are filtered by `useAuth()` in the navs; the API policy (`ensure_can_manage_users`,
 * `ensure_can_read_presence`, payroll policies) stays the authority.
 */
export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  ...DESTINATIONS.map(({ href, label, icon }) => ({ href, label, icon })),
  { href: "/leave", label: "Leave", icon: CalendarDays },
  { href: "/overtime", label: "Overtime", icon: Timer },
  {
    href: "/payroll",
    label: "Payroll",
    icon: Banknote,
    roles: PAYROLL_VIEW_ROLES,
  },
  { href: "/payroll/my-payslips", label: "My payslips", icon: ReceiptText },
  {
    href: "/reports",
    label: "Reports",
    icon: ChartColumn,
    roles: REPORT_READ_ROLES,
  },
  {
    href: "/recruitment",
    label: "Recruitment",
    icon: BriefcaseBusiness,
    roles: RECRUITMENT_NAV_ROLES,
  },
  {
    href: "/command-center",
    label: "Command center",
    icon: MonitorDot,
    roles: COMMAND_CENTER_ROLES,
  },
  {
    href: "/settings/users",
    label: "Users",
    icon: UserCog,
    roles: MANAGE_USER_ROLES,
  },
];
