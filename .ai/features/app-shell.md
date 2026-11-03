# Feature: App shell & navigation (shadcn-admin structural port)

- **Status:** done (2026-11-03)
- **Phase:** post-8 (UX refactor)
- **Size:** M
- **Surfaces:** web
- **Links:** reference template `shadcn-admin/` (local clone of satnaing/shadcn-admin, untracked) · `.kilo/skills/workpulse-ui/SKILL.md` (identity stays authoritative) · `.ai/conventions.md` → Presentation rules · `apps/web/components/destinations.ts` (nav catalog) · ADR 0005 (web stack)

## Problem

The dashboard shell is a flat horizontal top nav carrying 13 role-filtered
destinations in a single row (`overflow-x-auto`), with no grouping, no
collapsible structure and no keyboard-first entry point. On common laptop
widths the row scrolls sideways; on mobile the same flat list repeats in a
drawer. As every roadmap phase added destinations, the shell never gained
information architecture. The local `shadcn-admin` template demonstrates the
structure this class of product needs — a persistent collapsible sidebar with
grouped, collapsible nav, a slim header with a sidebar trigger and a command
palette (`⌘K`) — but its visual identity, stack (TanStack Router) and feature
pages are not ours. This feature ports the **structure and interaction
patterns** onto the existing WorkPulse identity and Next.js App Router.

## Business rules

- **The WorkPulse design identity is authoritative and unchanged.** The
  template contributes layout structure and interaction patterns only — never
  tokens, typography, radii, colors or copy. Everything remains inside
  `workpulse-ui`'s dials (VARIANCE 2–4 / MOTION 1–3 / DENSITY 6–8).
- **One nav catalog.** `apps/web/components/destinations.ts` stays the single
  source of truth (hrefs, icons, role gates, copy keys); the sidebar,
  command palette and any future nav consumer read from it. Role filtering
  stays a web-side mirror of API policies — the UI hides, `policies.py`
  decides.
- **New primitives are vendored through `components/ui/` and retuned to
  project tokens** (loaded with the `shadcn` skill): no template styling leaks
  through, no call-site metric overrides, `className` layout-only.
- **Wire values are never rendered; nav labels come from the `nav` message
  namespace (en + vi)** — new group titles get keys in both locales.
- **Navigation state is accessible:** `aria-current="page"` on the active
  item, keyboard-reachable collapse controls, focus trapped in the mobile
  sheet, skip-to-main link, and the collapsed rail exposes labels via
  tooltips _and_ accessible names.
- **Z-index stays the three-tier scale** (`z-10` local / `z-40` app chrome /
  `z-50` Radix overlays). The sidebar is app chrome; the mobile sheet and
  command dialog are overlays.
- **Both themes, reduced motion, and the `min-h-dvh` / no-horizontal-page-scroll
  floor** hold for the new shell (workpulse-ui §2, §10).
- **SSR safety:** collapsed state hydrates without mismatch (cookie-based
  initial state, as in the template's `SidebarProvider`), and the shell never
  renders user data that `AuthGate` has not resolved.

## Scope

- **Sidebar shell** replacing the top `MainNav` + `MobileNav`:
  - Collapsible desktop sidebar: expanded (icon + label) ↔ collapsed icon rail
    with tooltips; collapse toggle in the header; state persisted in a cookie.
  - **Grouped nav** sections (group titles from i18n); in the collapsed icon
    rail the group labels hide and every destination stays reachable via its
    icon + tooltip (template `nav-group.tsx` pattern).
  - Role filtering and the longest-prefix active rule (`activeNavHref`)
    preserved exactly.
  - Mobile: the sidebar becomes a focus-trapped sheet (template
    `SidebarProvider`/`isMobile` behavior), replacing `mobile-nav.tsx`.
  - Skip-to-main link (template `skip-to-main.tsx`).
- **Slim header:** brand mark, sidebar trigger, command-palette trigger
  (search affordance), and the existing controls unchanged in behavior —
  `LocaleSwitcher`, `NotificationBell`, `ThemeToggle`, `UserMenu`.
- **Command palette (`⌘K` / `Ctrl+K`):** fuzzy navigation to every nav
  destination the user's role can see, plus theme toggle. Openable from the
  header trigger; Escape closes; full keyboard operation (arrow/enter).
- **New `components/ui/` primitives** (via `shadcn` skill, retuned):
  `sidebar`, `command`, `tooltip`, `separator` (sheet already
  present; no `breadcrumb` — decision 3).
- **i18n:** `nav` group titles and command-palette strings in `en` + `vi`.
- **Tests:** unit/component tests for role filtering, active-state matching and
  collapsed-rail rendering; e2e smoke for shell navigation on desktop and
  mobile viewports; accessibility floor checked in both themes.

## Out of scope

- **Any visual identity change** — tokens, Fira type, radius scale, card
  chrome, status colors all stay. A template color/radius/font "cleanup" is a
  separate feature with its own ADR.
- **Data-table / chart / stats-card component patterns** (template
  `data-table/`) — not selected in this round.
- **Auth, settings and feature-page restyle** — page content keeps its current
  layout under `PageHeader`; only the shell around it changes.
- Template features we do not have: team switcher (one account = one tenant),
  config drawer, `nav-user` billing/upgrade items, help center, chats, apps
  pages.
- No route renames, no new destinations, no API/worker/event changes.
- The template's `cn`/`lib/utils` conventions are not adopted — `cn` stays
  imported from the `cn` package per project rule.

## Surfaces & files

| Surface     | Location                                                                                                                                                                                                       |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web shell   | `apps/web/app/(dashboard)/layout.tsx` (recompose)                                                                                                                                                              |
| Web feature | `apps/web/features/shell/` (sidebar nav, command palette, skip link — new)                                                                                                                                     |
| Web shared  | `apps/web/components/destinations.ts` (extend: groups), `components/ui/sidebar.tsx`, `components/ui/command.tsx`, `components/ui/tooltip.tsx` (existing), `components/ui/separator.tsx`, `hooks/use-mobile.ts` |
| Replaced    | `apps/web/components/main-nav.tsx`, `apps/web/components/mobile-nav.tsx` (removed once the sidebar lands)                                                                                                      |
| i18n        | `apps/web/messages/en/nav.json` (+ `command` strings), `apps/web/messages/vi/…`                                                                                                                                |
| Tests       | `apps/web/tests/layout/shell-navigation.test.tsx` (new) · `tests/e2e/shell-navigation.spec.ts` (new)                                                                                                           |
| Reference   | `shadcn-admin/src/components/layout/` (`app-sidebar`, `nav-group`, `header`, `main`, `skip-to-main`, `command-menu`), `shadcn-admin/src/components/ui/sidebar.tsx`                                             |

## Roles & access

No API surface changes. Role-gated destinations (Payroll, Reports,
Recruitment, Performance, Command center, Users) keep their existing role
lists in `destinations.ts`; the sidebar and the command palette both filter by
the same predicate, so a role never sees a destination it cannot open. Page
guards and API policies remain the authority.

## API contract

None — web-only refactor.

## Events

None.

## Edge cases

- **Role subsets:** an `employee` sees the short nav (Dashboard, Employees,
  Attendance, Leave, Overtime, My payslips, My documents) while an admin sees
  all 13 — both must render sensibly in expanded, collapsed and mobile states;
  an empty group (all items role-hidden) is not rendered.
- **Hydration:** the cookie-persisted collapsed state must produce identical
  SSR and first client render (or render after mount) — no hydration warnings.
- **Active matching:** nested routes (`/payroll/my-payslips/{id}`) mark only
  their own item via the existing longest-prefix rule; query strings ignored.
- **vi labels** run longer than en — nav rows must not clip or wrap
  awkwardly at the expanded sidebar width.
- **Mobile sheet** closes on navigation (template `setOpenMobile(false)` on
  click) and returns focus to the trigger.
- **Command palette with zero results** shows the standard empty state copy;
  it never renders destinations the role cannot see (including in fuzzy match).
- **`⌘K` interception:** only when no text input has focus; IME composition
  must not be swallowed.
- **13 items + group headers** must fit typical 768–900px-tall viewports in the
  expanded sidebar without page scroll; the sidebar itself may scroll.
- **The untracked nested clone** `shadcn-admin/` must never be committed
  (decision below).

## Acceptance criteria

- [x] **Shell (desktop):** given an authenticated user at ≥768px, when the
      dashboard loads, then a grouped sidebar renders with the role-filtered
      destinations, the current route marked `aria-current="page"`, and the header
      carries the sidebar trigger.
- [x] **Shell (collapse):** when the user collapses the sidebar, then it
      becomes an icon rail with tooltips/accessible names, the choice persists
      across reload via a cookie, and no destination becomes unreachable.
- [x] **Shell (mobile):** at <768px, when the nav trigger is used, then the
      sidebar opens as a focus-trapped sheet, closes on navigation and on Escape,
      and the page never scrolls horizontally.
- [x] **Shell (roles):** given an `employee` session, when the sidebar and the
      command palette render, then role-gated destinations are absent from both,
      and their pages still redirect when visited directly.
- [x] **Command palette:** given focus outside a text input, when `⌘K`/`Ctrl+K`
      or the header search affordance is used, then a dialog lists only the
      visible destinations, fuzzy-matches as the user types, navigates on Enter,
      and closes on Escape — fully keyboard-operable.
- [x] **Command palette (empty):** given a query matching nothing, when the
      palette renders results, then an empty state appears (never a blank list).
- [x] **Identity parity:** when the shell renders in both themes, then colors,
      type, radii and control metrics come only from existing tokens/primitives —
      verified by screenshot at 375 / 768 / 1440 and no new hardcoded hexes or
      one-off spacing values in the diff.
- [x] **i18n:** switching locale to `vi` translates every group title, nav
      label and palette string; no string is hardcoded in components.
- [x] **A11y floor:** skip-to-main works, focus is visible on every shell
      control, icon-only buttons have accessible names, and reduced-motion
      disables the shell's transition effects without breaking state changes.
- [x] **E2E:** `tests/e2e/shell-navigation.spec.ts` covers navigate-via-sidebar
      and navigate-via-palette on desktop, and sheet navigation on mobile. Gate
      result 2026-11-03: API 2125, web 454, e2e 30/31 — the single e2e failure is
      the pre-existing overtime-directory >100-employee backlog item recorded in
      `tasks/TODO.md` → Later, not this feature (`make check-load` cannot run on
      this host: no `k6`).
- [ ] _(Tenant isolation & denied role: no mutating surface — covered by the
      roles criterion above; API policies untouched.)_

## Decisions (PO, 2026-11-03)

1. **Nav grouping taxonomy — six groups as proposed** (nav order preserved
   within groups): _Overview_ (Dashboard) · _Workforce_ (Employees, Attendance,
   Leave, Overtime, Performance) · _Payroll_ (Payroll, My payslips) · _Hiring_
   (Recruitment) · _Insight_ (Reports, Command center) · _Administration_
   (Users, Organization, My documents).
2. **Command palette scope — navigation + theme toggle.** Fuzzy navigation to
   role-visible destinations plus theme switch. No locale switch or inbox jump
   in the palette.
3. **Breadcrumbs — none.** The active sidebar item + `PageHeader` answer "where
   am I?"; the header stays slim. (Breadcrumb primitive dropped from scope.)
4. **User menu stays in the header** (current `UserMenu` behavior); no
   template-style sidebar footer user card.
5. **`cmdk` approved** as the palette's only new runtime dependency (root
   AGENTS.md rule 10 justification: the shadcn `Command` primitive wraps it and
   there is no lighter maintained alternative for accessible combobox lists).
6. **Template hygiene — keep `shadcn-admin/` untracked while the port is built,
   add it to `.gitignore`, delete the clone once the port lands.** It must
   never be committed.

## Plan

Ordered, file-level. One concern per change; verify each step before the next.
Load the `shadcn` skill before step 2 and the `workpulse-ui` pre-flight before
step 9.

1. **Hygiene + dependency.** Add `shadcn-admin/` to `.gitignore`; `pnpm add
cmdk` in `apps/web` (the only new runtime dep, decision 5). Verify:
   `git status` shows the clone ignored; `cmdk` present in
   `apps/web/package.json`.
2. **UI primitives.** Vendor `components/ui/sidebar.tsx`, `command.tsx`,
   `tooltip.tsx`, `separator.tsx` via the `shadcn` skill
   (audit what already exists first); retune all of them to project tokens —
   sidebar surface uses existing background/accent/muted vars (no new
   `--sidebar-*` palette), z-tier scale respected (mobile sheet `z-50`, header
   `z-40`), control metrics (`h-9`, `rounded-md`, focus ring) match
   `.ai/conventions.md`. Verify: `pnpm run typecheck`.
3. **Nav catalog + groups.** Extend `apps/web/components/destinations.ts` with
   a `NAV_GROUPS` structure (six groups per decision 1; group key type;
   each group lists NavKeys; empty-after-role-filter groups do not render).
   Keep `NAV_ITEMS`, `activeNavHref` and role lists untouched. Add/extend unit
   tests for grouping and role filtering. Verify: `pnpm run test` (web).
4. **Sidebar feature.** New `apps/web/features/shell/AppSidebar.tsx` +
   `nav-group.tsx` (adapted from `shadcn-admin/src/components/layout/`):
   Next `Link` + `usePathname` + `useTranslations("nav")` + `useAuth` role
   filter; expanded ↔ collapsed icon rail with tooltips + accessible names;
   collapsed-group fly-out dropdowns; mobile sheet closes on navigation and
   returns focus to the trigger; cookie-persisted collapsed state without
   hydration mismatch. Verify: `pnpm run typecheck && pnpm run test`.
5. **Command palette.** `apps/web/features/shell/CommandPalette.tsx` (trigger,
   shared `useHotkey` guard) + `command-palette-dialog.tsx` (cmdk dialog over
   role-visible destinations + theme toggle, lazy-loaded so `cmdk` stays out of
   the initial shell chunk); `⌘K`/`Ctrl+K` handler with text-input/IME guard;
   header search trigger; empty state; Escape/arrows/Enter semantics. Verify:
   component tests (filtering, role subset, empty query) + `pnpm run test`.
6. **Header + skip link.** Recompose the header inside
   `apps/web/app/(dashboard)/layout.tsx`: brand, `SidebarTrigger`, palette
   trigger, then existing `LocaleSwitcher` / `NotificationBell` /
   `ThemeToggle` / `UserMenu` unchanged; add `features/shell/SkipToMain.tsx`.
   Keep `h-16`, `z-40`, sticky semantics. Verify: `pnpm run typecheck`.
7. **Layout recomposition.** `(dashboard)/layout.tsx` becomes
   `AuthGate` → `SidebarProvider` → sidebar + slim header + `main`
   (`max-w-7xl` content wrapper and `min-h-dvh` floor preserved; main gains
   `id="main"` for the skip link). Remove `main-nav.tsx` and `mobile-nav.tsx`
   once nothing references them (`grep` for imports). Verify:
   `pnpm run lint`.
8. **i18n.** Add group titles + palette strings + new aria labels to
   `apps/web/messages/en/nav.json` (and `command` strings to the fitting
   namespace) and the `vi` mirrors. Verify: locale switch renders vi with no
   missing-key warnings.
9. **Tests + visual pass.** E2E `tests/e2e/shell-navigation.spec.ts` (sidebar
   nav, palette nav, mobile sheet nav, role subset). Run
   `apps/web` typecheck/lint/test, then `make check`. Screenshot both themes
   at 375 / 768 / 1440 + landscape; run the `workpulse-ui` §15 pre-flight and
   the a11y floor (§10).
10. **Record.** Tick acceptance criteria above, update `tasks/TODO.md`,
    note the `cmdk` dependency decision where conventions track deps, and
    delete the `shadcn-admin/` clone.
