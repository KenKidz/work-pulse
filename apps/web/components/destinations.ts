import {
  Banknote,
  BriefcaseBusiness,
  Building2,
  CalendarClock,
  CalendarDays,
  ChartColumn,
  IdCard,
  LayoutDashboard,
  MonitorDot,
  ReceiptText,
  Target,
  Timer,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";

import type { Role } from "@workpulse/api-client/index.schemas";

import { PERFORMANCE_NAV_ROLES } from "@/features/performance/roles";
import { PAYROLL_VIEW_ROLES } from "@/features/payroll/roles";
import { RECRUITMENT_NAV_ROLES } from "@/features/recruitment/roles";
import { REPORT_READ_ROLES } from "@/features/reports/roles";

/** The translation key (inside the `nav` namespace) for a destination. */
export type DestinationKey = "employees" | "attendance" | "organization";

export type Destination = {
  href: string;
  key: DestinationKey;
  icon: LucideIcon;
};

/**
 * The Phase 1 surfaces — single source of truth for the primary navigation,
 * the landing cards and the dashboard cards, so routes, icons and copy keys
 * cannot drift between them.
 */
export const DESTINATIONS: Destination[] = [
  { href: "/employees", key: "employees", icon: Users },
  { href: "/attendance", key: "attendance", icon: CalendarClock },
  { href: "/settings/organization", key: "organization", icon: Building2 },
];

export type NavKey =
  | "dashboard"
  | DestinationKey
  | "leave"
  | "overtime"
  | "payroll"
  | "myPayslips"
  | "myDocuments"
  | "reports"
  | "recruitment"
  | "performance"
  | "commandCenter"
  | "users";

export type NavItem = {
  href: string;
  key: NavKey;
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
  { href: "/dashboard", key: "dashboard", icon: LayoutDashboard },
  ...DESTINATIONS.map(({ href, key, icon }) => ({ href, key, icon })),
  { href: "/leave", key: "leave", icon: CalendarDays },
  { href: "/overtime", key: "overtime", icon: Timer },
  {
    href: "/payroll",
    key: "payroll",
    icon: Banknote,
    roles: PAYROLL_VIEW_ROLES,
  },
  { href: "/payroll/my-payslips", key: "myPayslips", icon: ReceiptText },
  { href: "/profile", key: "myDocuments", icon: IdCard },
  {
    href: "/reports",
    key: "reports",
    icon: ChartColumn,
    roles: REPORT_READ_ROLES,
  },
  {
    href: "/recruitment",
    key: "recruitment",
    icon: BriefcaseBusiness,
    roles: RECRUITMENT_NAV_ROLES,
  },
  {
    href: "/performance",
    key: "performance",
    icon: Target,
    roles: PERFORMANCE_NAV_ROLES,
  },
  {
    href: "/command-center",
    key: "commandCenter",
    icon: MonitorDot,
    roles: COMMAND_CENTER_ROLES,
  },
  {
    href: "/settings/users",
    key: "users",
    icon: UserCog,
    roles: MANAGE_USER_ROLES,
  },
];
