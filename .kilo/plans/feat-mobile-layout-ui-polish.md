# Mobile layout, UI consistency & navigation rework for `apps/web`

> **Status**: Draft
> **Author**: Plan Mode
> **Created**: 2026-09-30
> **Last Updated**: 2026-09-30
> **Related**: ADR 0005 (`.ai/decisions/0005-frontend-stack.md`) · `apps/web/AGENTS.md` · `.ai/conventions.md` `## TypeScript — apps/web` · root `AGENTS.md` rule 15
> **Written against commit**: `760ece1` (`refactor(api): replace dev-identity headers with session-based identity`) — working tree clean at planning time.

---

## Overview

The WorkPulse web frontend (`apps/web`) has a coherent token system and a consistent
desktop layout, but four list surfaces, the filter bars, the form dialogs and the
mobile shell lag behind patterns the same codebase already established on the
attendance surface. This plan brings every Phase 1 surface up to those established
patterns, adds a drawer-based mobile navigation shell, and applies a disciplined
visual polish pass that **preserves the existing identity** (Fira Sans + navy ink +
electric blue on paper) rather than redesigning it.

The end state: every list surface announces and supports horizontal scrolling on
narrow viewports, every row action meets the 44px touch target the sibling surface
already uses, filter bars and dialog field grids stack predictably below `sm`, the
mobile header is uncluttered behind a single menu trigger, and card chrome follows
one documented role-based rule instead of three competing ones.

---

## Goals & Non-Goals

### Goals

1. **Table scroll affordance everywhere** — all six list tables expose a named,
   keyboard-reachable horizontal scroll region plus the `sm:hidden` "Swipe sideways…"
   hint, matching the attendance exemplar. Pinned by tests.
2. **44px row-action touch targets on mobile** — the nine Edit/Archive/Restore buttons
   in the organization tables expand to `min-h-11` below `sm`, matching `ShiftsPanel`.
3. **Responsive filter-bar widths** — filter inputs fill each wrapped row below `sm`
   using the `w-full sm:w-…` shape the attendance filter row already demonstrates.
4. **Dialog field grids stack below `sm`** — form field pairs go single-column on
   phones so `SelectTrigger` values stop truncating at ~140px.
5. **Drawer-based mobile navigation** — below `md`, destinations live in a slide-in
   sheet behind one menu trigger; the awkward second header row is removed.
6. **Identity-preserving visual polish** — one mono family for data labels, one
   documented card-treatment rule by role, consistent card padding by role.

### Non-Goals

- **No palette, radius-scale or typography-family change.** `globals.css` tokens
  (`--background #f4f7fb`, `--foreground #172b4d`, `--primary #2563eb`, Fira Sans)
  stay as they are. This is a polish pass, not a rebrand.
- **No new npm dependency.** The drawer is composed from the already-installed
  `radix-ui` `Dialog` primitive. `apps/web/package.json` must not change.
- **No API, domain, routing, or data-fetching change.** Query hooks, schemas and
  `@workpulse/api-client` usage are untouched. No `orval` regeneration.
- **No Phase 2+ technology** (no WebSocket, no auth/RBAC work, no new state stores).
- **No new feature, domain, route, or component "for later".** Only the primitives
  this plan proves necessary (`components/ui/sheet.tsx`).
- **No dark-mode token work.** Both themes already resolve; this plan changes
  structure and layout classes only, and dark-mode contrast must be spot-checked,
  not re-tuned.
- **No e2e (Playwright) additions.** This is presentation-only; Vitest covers it.
- **No change to `packages/api-client/`** (generated — never hand-edit).

---

## Background & Context

### Design language (reconstructed from the traced path)

- **Audited surface**: `apps/web` — the `(dashboard)` shell and its Phase 1 surfaces
  (Dashboard, Attendance, Employees + `[id]`, Organization settings × 3).
- **Design sources**: `apps/web/app/globals.css` (the token set, light + dark),
  `apps/web/AGENTS.md`, `.ai/conventions.md`, `.ai/decisions/0005-frontend-stack.md`.
  No `DESIGN.md` exists in the repo — none documented.
- **Documented decisions**: ADR 0005 frontend stack; `app/` holds routes only;
  `"use client"` only on interactive leaves; shadcn primitives in `components/ui/`
  are "generated, then owned"; every data-fetching surface has loading/empty/error
  states; UI quality work follows the `ui-ux-pro-max` skill, redesigns also follow
  `frontend-design` (root `AGENTS.md` rule 15).
- **Governing owners and consumers**:
  - `components/ui/{table,button,badge,card,dialog,select,input,textarea,form,alert,spinner,sonner}.tsx`
  - `components/{page-header,empty-state,main-nav,destinations,theme-toggle}.tsx`
  - `app/globals.css` → `@theme inline` token bridge
- **Explicit exceptions**: None documented.

### Current state and why the change is needed

An audit of the traced rendering path found five source-provable defects and one
underspecified area (mobile nav). Each is a **presentation** problem with a
deterministic correction already exemplified inside this repo — no product intent
needs inventing.

| # | Problem | Proven by | Where |
|---|---------|-----------|-------|
| 1 | Four list tables have no mobile scroll affordance; their two siblings do | `AttendanceRecordsTable.tsx:162-167` and `ShiftsPanel.tsx:121-126` render an `sm:hidden` hint **and** pass `containerLabel`; `EmployeesTable.tsx:271`, `DepartmentsTable.tsx:108`, `PositionsTable.tsx:100`, `LocationsTable.tsx:100` render bare `<Table>` | `components/ui/table.tsx:11-27` only names/keyboard-enables the `overflow-x-auto` region when `containerLabel` is passed |
| 2 | Nine org row-action buttons are 24px tall at every viewport | `ShiftsPanel.tsx:157-158` pairs `size="xs"` with `className="min-h-11 sm:min-h-7"`; `DepartmentsTable.tsx:133,143,154`, `PositionsTable.tsx:123,133,144`, `LocationsTable.tsx:125,135,146` do not | `components/ui/button.tsx:24` defines `xs` as `h-6` |
| 3 | Filter inputs ignore the `w-full sm:…` convention their own rows use | `AttendanceRecordsTable.tsx:63,71` use `w-full sm:w-40` while `:105` uses bare `w-64`; `EmployeesTable.tsx:66` (`w-64 pl-8`), `:78,93,108,140` (`w-48`), `:123` (`w-40`) | Same shared `Input`/`Select`; widths are literal classes |
| 4 | Dialog field grids never collapse; selects render ~140px wide on a phone | `EmployeeFormDialog.tsx:172,236,287,344`, `TransferDialog.tsx:183`, `ShiftsPanel.tsx:262` all use `grid grid-cols-2` | `components/ui/dialog.tsx:46` sets `max-w-[calc(100%-2rem)]` with `p-6` → 295px of content at 375px |
| 5 | Two card treatments across sibling surfaces | Flat `rounded-md border-border shadow-none` on all five attendance data panels vs. inherited `rounded-xl … shadow-sm` (`card.tsx:10`) on `EmployeesTable.tsx:239`, `DepartmentsTable.tsx:77`, `PositionsTable.tsx:69`, `LocationsTable.tsx:69` | Both resolve through `components/ui/card.tsx:10` |
| 6 | Mobile nav is a permanently-scrolled second header row | `(dashboard)/layout.tsx:21-25` wraps `MainNav` in `overflow-x-auto`; 4 items ≈ 480px vs a 375px viewport, active item can sit off-screen | `components/main-nav.tsx` has no scroll-into-view and no affordance |

### Key existing patterns the executor must follow

- **Table mobile exemplar** — `apps/web/features/attendance/components/AttendanceRecordsTable.tsx:162-167`
  (hint paragraph + `containerLabel`), asserted by
  `apps/web/tests/attendance/attendance-records.test.tsx:66-71`.
- **Row-action touch-target exemplar** — `apps/web/features/attendance/components/ShiftsPanel.tsx:157-158`.
- **Narrow-viewport width exemplar** — `apps/web/features/attendance/components/AttendanceRecordsTable.tsx:63,71`.
- **Dialog primitive conventions** — `apps/web/components/ui/dialog.tsx` (`data-slot`
  naming, `cn`, `cva`, Radix `DialogPrimitive`, `showCloseButton` prop).
- **Client-leaf convention** — `apps/web/components/main-nav.tsx` and
  `apps/web/features/organizations/components/OrganizationNav.tsx`: `"use client"`,
  `usePathname()`, `aria-current="page"`, `focus-visible:ring-[3px] focus-visible:ring-ring/50`.
- **Shared-copy convention** — `apps/web/components/destinations.ts` is documented as
  "single source of truth for the primary navigation, the landing cards and the
  dashboard cards, so routes, labels and icons cannot drift between them".

---

## Requirements

### Functional

- F1: Every list table renders its `sm:hidden` "Swipe sideways…" hint and passes a
  `containerLabel` to `<Table>`; the four affected tables' tests pin both strings.
- F2: The nine org row-action buttons render `min-h-11 sm:min-h-7` and keep their
  existing `aria-label`s (tests query them by name).
- F3: Below `sm`, each filter input occupies the full row width; at `sm` and above
  the current fixed widths return unchanged.
- F4: Below `sm`, every dialog field-pair row renders one column; at `sm` and above
  two columns as today.
- F5: Below `md`, the header shows brand + menu trigger + theme toggle only. The
  menu trigger opens a sheet listing all four destinations with `aria-current="page"`
  on the active one; selecting a destination closes the sheet.
- F6: At `md` and above, navigation is unchanged (`MainNav` inline in the header).
- F7: Data labels marked `font-mono` render in Fira Code.
- F8: Every table-carrying card uses the flat data-panel treatment; link/reading
  cards keep the elevated treatment.

### Non-Functional

- **Performance**: no measurable change. `next/font/google` self-hosts Fira Code
  with `display: "swap"`; add only weights `400, 500, 600` (matching the Fira Sans
  load at `app/layout.tsx:6-11`). No client-side JS added to server pages; the
  sheet and menu trigger are client leaves.
- **Accessibility** (required by root `AGENTS.md` and the `ui-ux-pro-max` checklist
  that rule 15 mandates for this work):
  - horizontal scroll regions are named and `tabIndex=0` (via `containerLabel`);
  - the menu trigger carries an `aria-label` and `aria-expanded`;
  - the sheet exposes `DialogTitle` (Radix requires a title) and returns focus on close;
  - all tap targets on mobile are ≥ 44px (`min-h-11`);
  - keyboard focus stays visible (`focus-visible:ring-[3px] focus-visible:ring-ring/50`,
    the pattern used throughout `components/`);
  - `prefers-reduced-motion` stays respected — `globals.css:113-123` already
    enforces it globally; do not reintroduce motion outside that guard.
- **Responsive targets** (the `ui-ux-pro-max` pre-delivery list): 375px, 768px,
  1024px, 1440px. Below `sm` = stacked; `sm`–`md` = inline controls, scrollable
  tables; `md`+ = desktop nav and two-column dialogs.
- **i18n**: none — the app is English-only; copy is pinned by tests verbatim.
- **Observability**: none — presentation-only change, no logging.
- **Browser support**: unchanged (Next.js 16 default targets; `min-h-dvh` and
  `size-*` utilities are already in use).
- **No new dependencies** — enforced by `apps/web/AGENTS.md` ("declare every package
  you import… no relying on hoisted transitive deps") and root `AGENTS.md` rule 10.

---

## Affected Areas

| Area | Files / Modules | Impact |
| ---- | --------------- | ------ |
| Token bridge | `apps/web/app/globals.css`, `apps/web/app/layout.tsx` | Low |
| Card primitive (no edit — cited owner) | `apps/web/components/ui/card.tsx` | Low |
| New drawer primitive | `apps/web/components/ui/sheet.tsx` **(create)** | Medium |
| Nav data | `apps/web/components/destinations.ts` | Low |
| Desktop nav | `apps/web/components/main-nav.tsx` | Medium |
| Mobile nav | `apps/web/components/mobile-nav.tsx` **(create)** | Medium |
| Dashboard shell | `apps/web/app/(dashboard)/layout.tsx` | Medium |
| Attendance lists | `apps/web/features/attendance/components/{AttendanceRecordsTable,ShiftsPanel}.tsx` | Low |
| Employees | `apps/web/features/employees/components/{EmployeesTable,EmployeeFormDialog,TransferDialog}.tsx` | Medium |
| Organization | `apps/web/features/organizations/components/{DepartmentsTable,PositionsTable,LocationsTable}.tsx` | Medium |
| Tests | `apps/web/tests/{employees,organizations,layout}/*` | Medium |
| Docs | `.ai/conventions.md`, `apps/web/AGENTS.md`, `tasks/TODO.md` | Low |

---

## Dependencies

- **New npm/pnpm/yarn packages: none.** `radix-ui@^1.6.7` already provides the
  `Dialog` primitive the sheet composes (`apps/web/package.json:20`). Verify after
  every step: `git diff --stat apps/web/package.json` must be empty.
- **New env vars: none.**
- **New feature flags: none.** This is presentation-only and ships unconditionally.
- **New font asset**: `Fira_Code` via `next/font/google` (self-hosted at build, no
  runtime network call). Weights `400, 500, 600` only.
- **Migrations: none** (no schema, no data).

---

## Design Decisions

- **Decision 1 — Polish inside the existing token system; do not re-theme.**
  - **Rationale**: `globals.css:4-11` already encodes a deliberate direction ("Clear
    paper, deep navy ink, and electric blue for operational focus"), and the
    `ui-ux-pro-max` design-system profile for a workforce-operations dashboard
    resolves to **Data-Dense Dashboard** with **Fira Sans** — the family
    `app/layout.tsx:6-11` already loads. The tokens are validated; the defects are
    structural.
  - **Rejected**: adopt the profile's amber CTA accent `#D97706` — it would introduce
    a second accent role the product does not have and would break the pinned
    semantic palette (`--success/--warning/--info/--destructive`) in `globals.css`.
- **Decision 2 — Card chrome is differentiated by role, not unified.**
  - **Rationale**: `frontend-design` names "one border-radius on everything
    regardless of hierarchy" as a templated default. The repo already expresses two
    roles — flat operational data panels on the attendance surface, elevated
    reading/link cards on the dashboard and employee detail surfaces. The defect is
    that four *table* cards wear the elevated chrome.
  - **Rule to record**: **data panels** (a table, count board, activity feed, check-in
    panel, or shifts panel inside a bordered container) use
    `rounded-md border-border shadow-none`; **elevated cards** (destination/link
    cards, landing cards, employee Profile / Current-assignment cards) keep the
    primitive default `rounded-xl shadow-sm`.
  - **Rejected**: flatten everything to `rounded-md shadow-none` (kills the hover
    elevation on `dashboard/page.tsx:28`'s link cards) and round everything to
    `rounded-xl shadow-sm` (the templated default named above).
- **Decision 3 — `--font-mono` = Fira Code.**
  - **Rationale**: `font-mono` is already used at `EmployeesTable.tsx:284`,
    `EmployeeDetailCard.tsx:130`, `LocationsTable.tsx:117`, but `@theme inline`
    defines only `--font-sans`, so it falls back to the system mono stack — a
    foreign family beside Fira Sans. The design profile pairs Fira Code with Fira
    Sans for exactly this "data labels, technical, precise" role.
  - **Rejected**: keep the system mono stack (the source of the incoherence); add
    Fira Code as a *display* face for headings (would change the identity, which is
    a Non-Goal).
- **Decision 4 — Drawer/sheet for mobile nav, composed from the existing Radix `Dialog`.**
  - **Rationale**: it removes the permanently-scrolled second header row, needs no
    safe-area reservation in page content, and reuses `DESTINATIONS` as the single
    source of truth. The existing `DialogContent` (`dialog.tsx:46`) is hard-coded to
  centered (`top-[50%] left-[50%] translate-*`), so it **cannot** express a
    side-drawer — which is precisely the bar for introducing a new primitive.
  - **New primitive justification** (required by the improve-ui rules): the existing
    system cannot express a side-anchored overlay; `components/ui/sheet.tsx` is the
    shadcn-owned home; its only consumer is `components/mobile-nav.tsx` in the
    dashboard shell. It is composed from `radix-ui`'s `Dialog` exactly like
    `dialog.tsx`, so no dependency is added.
  - **Drawer side: `right`.** The trigger sits at the top-right beside `ThemeToggle`,
    so the drawer opens from the trigger's edge and leaves the brand/back affordance
    on the left uncovered.
  - **Rejected**: bottom tab bar (reserves content space, needs
    `pb-[env(safe-area-inset-bottom)]`, shifts identity toward a mobile app);
    "keep tabs and fix scrolling" (leaves destinations behind horizontal scrolling,
    which the user explicitly asked to rework); hamburger without a sheet primitive
    (hand-rolling an overlay is worse than composing the owned one).
- **Decision 5 — `NAV_ITEMS` moves into `components/destinations.ts`.**
  - **Rationale**: that file's own doc comment claims it is the single source of truth
    for navigation routes, labels and icons. `main-nav.tsx:13-17` currently rebuilds
    the list by prepending Dashboard to `DESTINATIONS`; `mobile-nav.tsx` must not
    duplicate that construction or the "cannot drift" guarantee breaks.
  - **Rejected**: export `ITEMS` from `main-nav.tsx` (leaves a client component as the
    data owner for a second client component — acceptable but weaker than the file
    that already owns the contract).

---

## Implementation Plan

Ordered, atomic steps — one commit each. **Run `pnpm run typecheck && pnpm run lint && pnpm run test` from `apps/web/` after every step**; the full gate is `make check` at the end.

### Step 1 — Add the Fira Code mono role

- **What**: define `--font-mono` so every existing `font-mono` usage resolves to Fira
  Code instead of the system mono stack.
- **Why**: employee codes and timezones are data labels set in `font-mono`
  (`EmployeesTable.tsx:284`, `EmployeeDetailCard.tsx:130`, `LocationsTable.tsx:117`)
  but currently render in a family foreign to the Fira Sans UI voice.
- **Files**:
  - `apps/web/app/layout.tsx` — modify. Import `Fira_Code` alongside `Fira_Sans`
    (line 3), add a `firaCode` constant mirroring `firaSans` (lines 6-11) with
    `variable: "--font-fira-code"`, and append `firaCode.variable` to the `body`
    `className` (line 18).
  - `apps/web/app/globals.css` — modify. In `@theme inline`, directly below
    `--font-sans: var(--font-fira-sans);` (line 78), add `--font-mono: var(--font-fira-code);`.
- **Code pattern** (mimic `apps/web/app/layout.tsx:6-11` exactly):

  ```tsx
  import { Fira_Code, Fira_Sans } from "next/font/google";

  const firaSans = Fira_Sans({
    subsets: ["latin"],
    weight: ["400", "500", "600"],
    variable: "--font-fira-sans",
    display: "swap",
  });
  const firaCode = Fira_Code({
    subsets: ["latin"],
    weight: ["400", "500", "600"],
    variable: "--font-fira-code",
    display: "swap",
  });
  ```

  and in `globals.css` `@theme inline`:

  ```css
  --font-sans: var(--font-fira-sans);
  --font-mono: var(--font-fira-code);
  ```

- **Naming**: `firaCode`, `--font-fira-code`, `--font-mono`.
- **Tests**: none (no behaviour; a snapshot test on a font variable is not worth it).
- **Verification**:
  - `pnpm run typecheck` (from `apps/web/`)
  - `pnpm run lint`
  - `pnpm run test`
  - Manual QA: `pnpm run dev`, open `/employees`, confirm `EMP-00001` in the Code
    column renders in Fira Code (distinct from the surrounding Fira Sans); open
    `/settings/organization/locations` and confirm the timezone column matches it.
- **Commit message**: `style(web): use Fira Code for data-label mono role`

### Step 2 — Apply the data-panel card treatment to the four table cards

- **What**: change the four table-carrying cards from the elevated default to the flat
  data-panel chrome the attendance surface already uses.
- **Why**: Decision 2 — same content type (a table in a bordered container) currently
  renders in two different styles across sibling surfaces.
- **Files** (all are the same one-line change; the class string is identical in each):
  - `apps/web/features/employees/components/EmployeesTable.tsx:239`
  - `apps/web/features/organizations/components/DepartmentsTable.tsx:77`
  - `apps/web/features/organizations/components/PositionsTable.tsx:69`
  - `apps/web/features/organizations/components/LocationsTable.tsx:69`
- **Change** (exact strings):

  ```
  - <Card className="gap-0 overflow-hidden py-0">
  + <Card className="gap-0 overflow-hidden rounded-md border-border py-0 shadow-none">
  ```

- **Preserve**: `gap-0 overflow-hidden py-0` (they suppress card padding so the table
  bleeds to the edges — required). Do **not** touch the elevated cards:
  `apps/web/app/(dashboard)/dashboard/page.tsx:28`, `apps/web/app/page.tsx` (landing
  cards), `apps/web/features/employees/components/EmployeeDetailCard.tsx` (Profile and
  Current-assignment `<Card>`s) keep `rounded-xl shadow-sm`.
- **Naming**: no new identifiers.
- **Tests**: none new — no existing test asserts card classes. Run the suites to prove
  nothing broke.
- **Verification**:
  - `pnpm run test` (from `apps/web/`)
  - `pnpm run typecheck && pnpm run lint`
  - Manual QA at 1440px: `/employees`, `/settings/organization/departments|positions|locations`
    cards are flat (6px radius, no shadow); `/dashboard` destination cards keep 10px
    radius and `shadow-sm`, and gain `shadow-md` on hover.
- **Commit message**: `style(web): flatten table card chrome to the data-panel treatment`

### Step 3 — Table scroll affordance on the four remaining list tables

- **What**: add the `sm:hidden` hint paragraph and a `containerLabel` to the four list
  tables that lack them, mirroring the attendance exemplar.
- **Why**: Finding 1. Without `containerLabel`, `components/ui/table.tsx:15-17` renders
  the `overflow-x-auto` region with no `role`, no `aria-label` and no `tabIndex`, so on
  a phone the cut-off columns give no affordance and the region cannot be scrolled
  from the keyboard.
- **Files**:
  - `apps/web/features/employees/components/EmployeesTable.tsx` — modify: insert the
    hint `<p>` immediately before `<Table>` (line 271) and add the props to `<Table>`.
  - `apps/web/features/organizations/components/DepartmentsTable.tsx:108` — same.
  - `apps/web/features/organizations/components/PositionsTable.tsx:100` — same.
  - `apps/web/features/organizations/components/LocationsTable.tsx:100` — same.
  - `apps/web/tests/employees/employees-table.test.tsx` — add one test.
  - `apps/web/tests/organizations/departments.test.tsx` — add one test.
  - `apps/web/tests/organizations/positions.test.tsx` — add one test.
  - `apps/web/tests/organizations/locations.test.tsx` — add one test.
- **Code pattern** — copy `apps/web/features/attendance/components/AttendanceRecordsTable.tsx:162-167`
  verbatim, changing only the copy:

  ```tsx
            <p className="px-4 py-2 text-xs text-muted-foreground sm:hidden">
              Swipe sideways to see every employee field.
            </p>
            <Table
              aria-label="Employees"
              containerLabel="Employees. Scroll horizontally to read all columns."
            >
  ```

  Note the indentation differs per file (EmployeesTable's `<Table>` is at 12 spaces,
  the org tables at 10) — match the surrounding block.
- **Naming / copy (pinned — tests assert these strings verbatim)**:

  | File | `aria-label` | `containerLabel` | hint text |
  | --- | --- | --- | --- |
  | `EmployeesTable.tsx` | `Employees` | `Employees. Scroll horizontally to read all columns.` | `Swipe sideways to see every employee field.` |
  | `DepartmentsTable.tsx` | `Departments` | `Departments. Scroll horizontally to see every department field.` | `Swipe sideways to see every department field.` |
  | `PositionsTable.tsx` | `Positions` | `Positions. Scroll horizontally to see every position field.` | `Swipe sideways to see every position field.` |
  | `LocationsTable.tsx` | `Locations` | `Locations. Scroll horizontally to see every location field.` | `Swipe sideways to see every location field.` |

- **Tests** — mirror `apps/web/tests/attendance/attendance-records.test.tsx:64-71`.
  Add to each of the four test files a single case:

  ```tsx
  it("names and hints the horizontal scroll region", async () => {
    renderTable(); // existing helper in each file

    expect(
      screen.getByRole("region", {
        name: "Departments. Scroll horizontally to see every department field.",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Swipe sideways to see every department field."),
    ).toBeInTheDocument();
  });
  ```

  Cases to cover per file: (a) the region is exposed with the exact `containerLabel`;
  (b) the hint text renders. The happy-path render already exists in each suite, so
  use its mocked list fixture (e.g. `ENGINEERING` in `departments.test.tsx`, `ALICE`
  in `employees-table.test.tsx`) — the hint renders only in the non-empty branch.
- **Verification**:
  - `pnpm exec vitest run tests/organizations tests/employees` (from `apps/web/`)
  - `pnpm run typecheck && pnpm run lint && pnpm run test`
  - Manual QA at 375px: on each of the four surfaces the "Swipe sideways…" line is
    visible; scrolling the table horizontally works; tabbing into the table scrolls it.
- **Commit message**: `fix(web): expose mobile scroll affordance on all list tables`

### Step 4 — 44px row-action touch targets in the organization tables

- **What**: expand the nine Edit/Archive/Restore row buttons to `min-h-11 sm:min-h-7`.
- **Why**: Finding 2. `components/ui/button.tsx:24` makes `size="xs"` `h-6` (24px);
  `ShiftsPanel.tsx:157-158` deliberately overrides that on mobile and the org tables
  do not.
- **Files**:
  - `apps/web/features/organizations/components/DepartmentsTable.tsx` — lines 133, 143, 154
  - `apps/web/features/organizations/components/PositionsTable.tsx` — lines 123, 133, 144
  - `apps/web/features/organizations/components/LocationsTable.tsx` — lines 125, 135, 146
- **Change** — add exactly one prop to each of the nine `size="xs"` buttons, matching
  `apps/web/features/attendance/components/ShiftsPanel.tsx:158`:

  ```
    <Button
      variant="ghost"
      size="xs"
  +   className="min-h-11 sm:min-h-7"
      aria-label={`Edit ${department.name}`}
  ```

  Use the surrounding `aria-label` (`` `Edit ${…}` ``, `` `Archive ${…}` ``,
  `` `Restore ${…}` ``) to disambiguate the three occurrences per file — the
  `size="xs"` lines share indentation within a file.
- **Preserve**: `variant="ghost"`, `size="xs"`, every `aria-label` (the tests query by
  them — `departments.test.tsx` uses `name: /archive engineering/i` etc.), and the
  `disabled={…isPending}` bindings.
- **Naming**: no new identifiers.
- **Tests**: none new — the existing archive/restore/edit cases already cover these
  buttons by accessible name and must keep passing unchanged.
- **Verification**:
  - `pnpm exec vitest run tests/organizations` (from `apps/web/`)
  - `pnpm run typecheck && pnpm run lint && pnpm run test`
  - Manual QA at 375px on `/settings/organization/departments`: the Edit and
    Archive buttons are 44px tall; at 1024px they are 28px (`sm:min-h-7`).
- **Commit message**: `fix(web): expand org row-action buttons to 44px on mobile`

### Step 5 — Responsive filter-bar widths

- **What**: make filter inputs fill their wrapped row below `sm`, returning to the
  current fixed widths at `sm`.
- **Why**: Finding 3. At 375px the `w-64` search input (256px) sits inside a 343px row
  and the six fixed-width controls wrap into ragged rows.
- **Files**:
  - `apps/web/features/attendance/components/AttendanceRecordsTable.tsx:105` — modify.
  - `apps/web/features/employees/components/EmployeesTable.tsx:60-145` — modify
    (`FiltersRow` only).
- **Change**:

  1. `AttendanceRecordsTable.tsx:105` (the employee-id input, a direct flex item):

     ```
     -        className="w-64"
     +        className="w-full sm:w-64"
     ```

  2. `EmployeesTable.tsx:62-67` — the search input is wrapped in `<div className="relative">`
     (the `Search` icon is absolutely positioned inside it). Put the width on the
     wrapper so `w-full` resolves, and let the input fill it:

     ```
     -      <div className="relative">
     +      <div className="relative w-full sm:w-64">
             <Search className="absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
             <Input
               aria-label="Search employees"
               placeholder="Search name, email or code"
     -          className="w-64 pl-8"
     +          className="w-full pl-8"
     ```

  3. `EmployeesTable.tsx:78, 93, 108, 140` (department / position / location / manager
     `SelectTrigger`s):

     ```
     -        <SelectTrigger aria-label="Filter by department" className="w-48">
     +        <SelectTrigger aria-label="Filter by department" className="w-full sm:w-48">
     ```

     (same for `Filter by position`, `Filter by location`, `Filter by manager`)

  4. `EmployeesTable.tsx:123` (status `SelectTrigger`):

     ```
     -        <SelectTrigger aria-label="Filter by status" className="w-40">
     +        <SelectTrigger aria-label="Filter by status" className="w-full sm:w-40">
     ```

- **Preserve**: every `aria-label`, the `ANY` sentinel handling, and the
  "clearing a filter drops the parameter (`undefined`), never `null`" behaviour
  pinned by `employees-table.test.tsx` ("clearing a filter drops the parameter
  instead of sending null (422 regression)"). Do **not** change the date-pair grid at
  `AttendanceRecordsTable.tsx:59-72` — it is already responsive.
- **Naming**: no new identifiers.
- **Tests**: none new — the existing filter cases (`employees-table.test.tsx`
  "applies the q filter and the status filter to the list query", `attendance-records.test.tsx`
  filter cases) query by accessible name and must pass unchanged.
- **Verification**:
  - `pnpm exec vitest run tests/employees tests/attendance` (from `apps/web/`)
  - `pnpm run typecheck && pnpm run lint && pnpm run test`
  - Manual QA at 375px: on `/employees` each of the six filters occupies a full row
    (no ragged gaps); at 768px they sit inline at their original widths. Repeat for
    `/attendance` → "Attendance records" filter row.
- **Commit message**: `fix(web): stack filter controls full-width below the sm breakpoint`

### Step 6 — Stack dialog field pairs below `sm`

- **What**: collapse every dialog field-pair row to one column below `sm`.
- **Why**: Finding 4. At 375px `dialog.tsx:46` gives `max-w-[calc(100%-2rem)]` with
  `p-6`, so a `grid-cols-2` pair renders at ~140px per column and Radix
  `SelectTrigger` placeholders ("Select a department") truncate.
- **Files**:
  - `apps/web/features/employees/components/EmployeeFormDialog.tsx` — lines 172, 236, 287, 344
  - `apps/web/features/employees/components/TransferDialog.tsx` — line 183
  - `apps/web/features/attendance/components/ShiftsPanel.tsx` — line 262 (the
    `ShiftFormDialog` start/end time pair)
- **Change** — uniform rule, no judgment required per row:

  ```
  -            <div className="grid grid-cols-2 gap-4">
  +            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
  ```

  and for the shift time pair (keep its `gap-3`):

  ```
  -            <div className="grid grid-cols-2 gap-3">
  +            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
  ```

  The four `EmployeeFormDialog` rows are: first/last name (172), department/position
  (236), location/manager (287), employment type/hire date (344). Use the field
  names in the surrounding `FormField name="…"` to confirm you hit all four.
- **Preserve**: every field's `name`, `FormLabel`, `FormMessage`, the edit-mode guard
  (`{!isEdit ? (…) : null}`), and the `NO_MANAGER` sentinel. The `DialogContent`
  `className="max-h-[85vh] overflow-y-auto sm:max-w-xl"` stays as-is — the dialog
  already scrolls.
- **Naming**: no new identifiers.
- **Tests**: none new — `employee-form-dialog.test.tsx`, `transfer-dialog.test.tsx`,
  `shifts-panel.test.tsx` query fields by label and must pass unchanged.
- **Verification**:
  - `pnpm exec vitest run tests/employees tests/attendance` (from `apps/web/`)
  - `pnpm run typecheck && pnpm run lint && pnpm run test`
  - Manual QA at 375px: `/employees` → "New employee" — every field is full width and
    its placeholder is fully legible; at 768px the pairs return to two columns.
- **Commit message**: `fix(web): stack dialog field pairs below the sm breakpoint`

### Step 7 — Add `components/ui/sheet.tsx`

- **What**: add the shadcn-owned side-drawer primitive composed from `radix-ui`'s
  `Dialog`, following `dialog.tsx` conventions exactly.
- **Why**: Decision 4. `dialog.tsx:46` hard-codes centered positioning, so the
  existing primitive cannot express a side-anchored overlay. The only consumer is
  `components/mobile-nav.tsx` (Step 8).
- **Files**:
  - `apps/web/components/ui/sheet.tsx` — **create**.
- **Requirements for the new file** (the executor must satisfy all of these):
  - `"use client"` at the top, like `dialog.tsx` would if it were not already
    client-marked through its imports — actually `dialog.tsx:1` **does** start with
    `"use client"`. Copy that.
  - Import `* as React from "react"`, `{ cn } from "cn"`, `{ XIcon } from "lucide-react"`,
    `{ Dialog as DialogPrimitive } from "radix-ui"` — the exact import shape of
    `apps/web/components/ui/dialog.tsx:1-6`.
  - `data-slot` values follow the dialog naming scheme: `"sheet"`, `"sheet-trigger"`,
    `"sheet-portal"`, `"sheet-overlay"`, `"sheet-content"`, `"sheet-header"`,
    `"sheet-footer"`, `"sheet-title"`, `"sheet-description"`.
  - Compose from `DialogPrimitive.Root / .Trigger / .Portal / .Overlay / .Content /
    .Title / .Description` — **do not** add an npm dependency and **do not** import
    from `radix-ui/react-dialog` directly.
  - `SheetContent` takes a `side` prop (`"right" | "left" | "top" | "bottom"`,
    default `"right"`) implemented with `cva` (already a dependency — see
    `apps/web/components/ui/button.tsx:2`) and renders the same overlay treatment as
    `dialog.tsx:29-37`. The content panel must be fixed to the chosen edge and
    full-height for `left`/`right` (e.g. `inset-y-0 right-0 h-full w-3/4 border-l
    sm:max-w-sm` for `side="right"`), with the same entry/exit animation classes as
    `dialog.tsx` uses.
  - Include a close button identical in behaviour to `dialog.tsx:60-71`
    (`XIcon`, `sr-only` "Close"), behind a `showCloseButton` prop defaulting to `true`.
  - `SheetTitle` and `SheetDescription` wrap `DialogPrimitive.Title/Description` —
    Radix requires a title in the content for accessibility.
- **Preserve**: nothing existing changes. `components/ui/dialog.tsx` is not edited.
- **Naming** (exact exports): `Sheet`, `SheetTrigger`, `SheetPortal`, `SheetOverlay`,
  `SheetContent`, `SheetHeader`, `SheetFooter`, `SheetTitle`, `SheetDescription`,
  `SheetClose`.
- **Tests**: none in isolation — Step 9 covers it through `mobile-nav`.
- **Verification**:
  - `pnpm run typecheck && pnpm run lint && pnpm run test`
  - `git diff --stat apps/web/package.json` → empty (no dependency added)
  - Manual QA: the sheet is exercised in Step 8's QA.
- **Commit message**: `feat(web): add sheet primitive for the mobile nav drawer`

### Step 8 — Rework the dashboard shell for drawer navigation

- **What**: remove the second header row; below `md` render a menu trigger that opens
  the sheet with the four destinations.
- **Why**: Finding 6 / Decision 4. The current row is permanently scrolled at 375px
  and the active item can sit off-screen.
- **Files**:
  - `apps/web/components/destinations.ts` — modify: add `NAV_ITEMS`.
  - `apps/web/components/main-nav.tsx` — modify: consume `NAV_ITEMS`, drop the local
    `ITEMS` construction.
  - `apps/web/components/mobile-nav.tsx` — **create** (client leaf).
  - `apps/web/app/(dashboard)/layout.tsx` — modify: drop the `md:hidden` row, mount
    `<MobileNav />`.
- **Changes**:

  1. `apps/web/components/destinations.ts` — append below `DESTINATIONS` (the file
     already imports `LayoutDashboard`-equivalent icons; add `LayoutDashboard` to the
     existing `lucide-react` import at line 1):

     ```ts
     /**
      * Every primary navigation destination — Dashboard plus the Phase 1
      * surfaces — so the desktop nav and the mobile drawer cannot drift.
      */
     export const NAV_ITEMS = [
       { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
       ...DESTINATIONS.map(({ href, label, icon }) => ({ href, label, icon })),
     ];
     ```

     (this is the exact construction currently at `main-nav.tsx:13-17`)

  2. `apps/web/components/main-nav.tsx` — replace lines 13-17 with
     `import { NAV_ITEMS } from "@/components/destinations";` and iterate `NAV_ITEMS`.
     Keep `ITEMS` out of this file. Everything else (the `active` derivation from
     `usePathname()`, `aria-current="page"`, the `after:` underline) is unchanged.

  3. `apps/web/components/mobile-nav.tsx` — **create**, following the client-leaf
     conventions of `apps/web/components/main-nav.tsx`:

     ```tsx
     "use client";

     /**
      * Mobile navigation drawer for the dashboard shell. Client leaf: the open
      * state is local, the active item derives from the pathname, and the
      * destinations come from the shared catalog so they cannot drift from the
      * desktop nav.
      */

     import { Menu } from "lucide-react";
     import Link from "next/link";
     import { usePathname } from "next/navigation";
     import { useState } from "react";
     import { cn } from "cn";

     import { NAV_ITEMS } from "@/components/destinations";
     import { Button } from "@/components/ui/button";
     import {
       Sheet,
       SheetContent,
       SheetDescription,
       SheetHeader,
       SheetTitle,
       SheetTrigger,
     } from "@/components/ui/sheet";

     export function MobileNav() {
       const pathname = usePathname();
       const [open, setOpen] = useState(false);

       return (
         <Sheet open={open} onOpenChange={setOpen}>
           <SheetTrigger asChild>
             <Button
               variant="ghost"
               size="icon-sm"
               className="size-10 rounded-sm"
               aria-label="Open navigation menu"
               aria-expanded={open}
             >
               <Menu aria-hidden="true" />
             </Button>
           </SheetTrigger>
           <SheetContent side="right">
             <SheetHeader>
               <SheetTitle>Menu</SheetTitle>
               <SheetDescription>Go to a WorkPulse surface.</SheetDescription>
             </SheetHeader>
             <nav aria-label="Main" className="flex flex-col gap-1 px-4 pb-6">
               {NAV_ITEMS.map((item) => {
                 const active =
                   pathname === item.href || pathname.startsWith(`${item.href}/`);
                 return (
                   <Link
                     key={item.href}
                     href={item.href}
                     aria-current={active ? "page" : undefined}
                     onClick={() => setOpen(false)}
                     className={cn(
                       "flex min-h-11 items-center gap-3 rounded-sm px-3 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
                       active
                         ? "bg-accent text-accent-foreground"
                         : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                     )}
                   >
                     <item.icon className="size-4" aria-hidden="true" />
                     {item.label}
                   </Link>
                 );
               })}
             </nav>
           </SheetContent>
         </Sheet>
       );
     }
     ```

     Requirements this satisfies: `min-h-11` on every destination (44px tap target),
     `aria-current="page"` on the active item (same contract as `main-nav.tsx:26`),
     `focus-visible:ring-[3px] focus-visible:ring-ring/50` (the repo-wide focus
     pattern), icons `aria-hidden="true"` beside visible text, and closing on navigate.

  4. `apps/web/app/(dashboard)/layout.tsx` — delete the block at lines 21-25:

     ```tsx
     {/* On small screens the nav moves to its own scrollable row. */}
     <div className="border-t border-border md:hidden">
       <div className="mx-auto max-w-7xl overflow-x-auto px-4 sm:px-6">
         <MainNav className="h-12 w-max" />
       </div>
     </div>
     ```

     and change the `ml-auto` group (line 18-20) to mount both controls:

     ```tsx
     <div className="ml-auto flex items-center">
       <MobileNav />
       <ThemeToggle />
     </div>
     ```

     Keep `MainNav` at `className="hidden h-16 md:flex"` (line 15) — desktop is
     unchanged (F6). Add `import { MobileNav } from "@/components/mobile-nav";`.
- **Preserve**: the sticky header (`sticky top-0 z-40 border-b border-border bg-background`),
  the brand `Link` to `/dashboard`, `ThemeToggle`, and the `main` container's
  `max-w-7xl px-4 py-7 sm:px-6 sm:py-9`. Do not change the breakpoint (`md`).
- **Naming**: `MobileNav`, `NAV_ITEMS`, `LayoutDashboard`, `Menu`.
- **Tests**: covered in Step 9 (this step lands with them in the same commit series,
  but Step 8's commit may precede — keep `pnpm run test` green either way since no
  existing test covers the shell).
- **Verification**:
  - `pnpm run typecheck && pnpm run lint && pnpm run test`
  - Manual QA at 375px: the header is one row (brand, menu icon, theme toggle) with no
    horizontal scrolling; tapping the menu icon opens a right-side drawer listing
    Dashboard, Employees, Attendance, Organization; the current route is highlighted;
    tapping a destination navigates and closes the drawer; Escape closes it. At 768px
    and 1440px the inline `MainNav` is visible and no menu icon is rendered.
- **Commit message**: `feat(web): replace the mobile nav row with a drawer menu`

### Step 9 — Cover the mobile nav with tests

- **What**: add a test file for `MobileNav` (and one case pinning the desktop nav's
  unchanged behaviour).
- **Why**: root `AGENTS.md` rule 5 — every business-facing change needs tests on every
  surface it touches, with a happy path and a failure/edge case.
- **Files**:
  - `apps/web/tests/layout/mobile-nav.test.tsx` — **create**.
- **Test cases** (all four are required):
  1. **Happy path** — the trigger renders with `aria-label="Open navigation menu"` and
     `aria-expanded="false"`; clicking it opens the drawer and lists all four
     destinations with the correct `href`s (`/dashboard`, `/employees`, `/attendance`,
     `/settings/organization`) taken from `NAV_ITEMS`.
  2. **Active state** — with `usePathname` mocked to `/attendance`, the Attendance
     link carries `aria-current="page"` and the others do not.
  3. **Interaction** — clicking a destination closes the drawer (assert the trigger's
     `aria-expanded` returns to `"false"`, or that the dialog content is gone).
  4. **Desktop preservation** — `MainNav` still renders its four links with the same
     hrefs when rendered directly (proves the `NAV_ITEMS` extraction did not change
     the desktop contract).
- **Code pattern** — follow the render/QueryClient harness style of
  `apps/web/tests/organizations/departments.test.tsx` for the wrapper, but this
  component has no API dependency, so no `@workpulse/api-client` mock is needed.
  Mock only the router:

  ```tsx
  vi.mock("next/navigation", () => ({
    usePathname: () => "/attendance",
  }));
  ```

  Query by accessible name throughout (`screen.getByRole("button", { name: "Open navigation menu" })`,
  `screen.getByRole("link", { name: "Attendance" })`) — the pattern the rest of
  `apps/web/tests/` uses.
- **Verification**:
  - `pnpm exec vitest run tests/layout` (from `apps/web/`)
  - `pnpm run typecheck && pnpm run lint && pnpm run test`
- **Commit message**: `test(web): cover the mobile nav drawer and nav data extraction`

### Step 10 — Record the presentation conventions and close out docs

- **What**: write the rules this plan establishes into the convention docs and tick
  the backlog, per the root `AGENTS.md` definition of done.
- **Why**: these are new, expensive-to-reverse presentation conventions (card chrome
  by role, dialog stacking, table affordance) and must not be re-derived.
- **Files**:
  - `.ai/conventions.md` — modify, inside `## TypeScript — apps/web` (starts line 48),
    directly after the "Visual design:" bullet. Add a `### Presentation rules`
    subsection recording:
    1. **Card chrome by role** — data panels (a table, count board, activity feed,
       check-in panel or shifts panel inside a bordered container) use
       `rounded-md border-border shadow-none`; elevated cards (destination/link cards,
       landing cards, reading cards) keep the `card.tsx` default `rounded-xl shadow-sm`.
    2. **List tables** always pass `containerLabel` to `components/ui/table.tsx` and
       render the `sm:hidden` "Swipe sideways to see every … field." hint above it
       (exemplar: `AttendanceRecordsTable.tsx:162-167`).
    3. **Row actions** in tables carry `className="min-h-11 sm:min-h-7"` on
       `size="xs"` buttons (exemplar: `ShiftsPanel.tsx:157-158`).
    4. **Filter controls** use `w-full sm:w-…` so they stack below `sm`
       (exemplar: `AttendanceRecordsTable.tsx:63,71`).
    5. **Dialog field pairs** use `grid-cols-1 gap-… sm:grid-cols-2`.
    6. **Mono data labels** (`employee_code`, timezones) use `font-mono`, which
       resolves to Fira Code.
  - `apps/web/AGENTS.md` — modify, in `## Rules`: add one bullet pointing at the
    presentation rules in `.ai/conventions.md` so web contributors find them.
  - `tasks/TODO.md` — modify: tick the UI/mobile item if one exists; otherwise add a
    line recording this change. Read the file first and match its existing format.
- **Preserve**: do not rewrite unrelated sections of these docs.
- **Verification**:
  - `make check` from the repo root (runs `check-api` + `check-web` + `check-e2e`) —
    must pass. If `check-e2e` needs Docker and the DB is not up, run `make db-up
    db-migrate db-seed` first (the Makefile's own prerequisite chain).
  - `git diff --stat apps/web/package.json` → empty.
- **Commit message**: `docs(web): record presentation rules for cards, tables and forms`

---

## Reusable Patterns the Executor Must Follow

| Pattern | Canonical example |
| ------- | ----------------- |
| Table mobile affordance (hint + `containerLabel`) | `apps/web/features/attendance/components/AttendanceRecordsTable.tsx:162-167` |
| Its pinned test | `apps/web/tests/attendance/attendance-records.test.tsx:64-71` |
| Row-action 44px tap target | `apps/web/features/attendance/components/ShiftsPanel.tsx:157-158` |
| Narrow-viewport input widths | `apps/web/features/attendance/components/AttendanceRecordsTable.tsx:63,71` |
| Flat data-panel card chrome | `apps/web/features/attendance/components/AttendanceRecordsTable.tsx:129` |
| Elevated card chrome (leave alone) | `apps/web/app/(dashboard)/dashboard/page.tsx:28` |
| shadcn primitive conventions (`data-slot`, `cn`, `cva`, Radix) | `apps/web/components/ui/dialog.tsx`, `apps/web/components/ui/button.tsx` |
| Client-leaf nav with `aria-current` | `apps/web/components/main-nav.tsx:16-42` |
| Shared destination catalog | `apps/web/components/destinations.ts` |
| Focus ring | `focus-visible:ring-[3px] focus-visible:ring-ring/50` (used in every interactive component) |
| Test harness with mocked api-client | `apps/web/tests/organizations/departments.test.tsx` |

**Anti-patterns to avoid** (state explicitly so the executor does not invent):
- Do **not** edit `packages/api-client/**` — generated by orval.
- Do **not** add an npm package; `radix-ui` already provides `Dialog`.
- Do **not** change `globals.css` color tokens, the `--radius` scale, or the
  `prefers-reduced-motion` block.
- Do **not** touch the four `EmployeeFormDialog` field *labels*, the `ANY`/`NO_MANAGER`
  sentinels, or the "clearing a filter drops the parameter (`undefined`), never `null`"
  logic — tests pin them.
- Do **not** unify card padding to one value; it is differentiated by role (Decision 2).
- Do **not** add a `Sheet`-like wrapper inside `features/` — primitives live in
  `components/ui/`.
- Do **not** add Playwright specs for this change.
- Do **not** rename `DESTINATIONS` or alter its shape — the landing page and dashboard
  cards consume it.

---

## Alternatives Considered

| Option | Pros | Cons | Why not chosen |
| ------ | ---- | ---- | -------------- |
| Re-theme with the design-system palette (amber CTA, `#1E40AF`) | Fresh look | Breaks the pinned semantic palette and the documented "electric blue for operational focus" identity; out of scope for a polish pass | Identity change is an explicit Non-Goal |
| Bottom tab bar for mobile nav | Best thumb reach, zero nav latency | Reserves content space, needs `env(safe-area-inset-bottom)`, shifts toward a mobile-app identity | The user chose the drawer; also the higher-risk shell change |
| Keep the scrollable tab row, only fix scrolling | Smallest diff | Destinations stay behind horizontal scrolling — the defect the user asked to rework | Rejected by the user's scope choice |
| Hand-roll the drawer inside `mobile-nav.tsx` | No new file | Duplicates overlay/focus/animation behaviour already owned by `components/ui/`; next surface needing a drawer duplicates it again | Violates "prefer existing abstractions" and the primitive-ownership rule |
| One global card padding/radius | Looks uniform | "One border-radius on everything regardless of hierarchy" is a templated default (`frontend-design`); it would flatten the dashboard link cards' hover elevation | Decision 2 |
| Collapse only the select rows in dialogs, leave name/time pairs | Slightly denser forms on mobile | Two rules to remember instead of one; the cheap executor would have to judge which rows qualify | Uniform rule is deterministic |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
| ---- | ---------- | ------ | ---------- |
| `next/font/google` fails to fetch Fira Code in a sandbox/offline build | Low | Build failure at Step 1 | `next/font` self-hosts at build; if the build environment is offline, `next build` will report it immediately — revert Step 1 only (it is isolated in its own commit) and record the limitation |
| Tests break because class strings moved | Medium | Red suite | Each step runs `pnpm run test` before commit; the existing tests query by accessible name, not class (verified in `employees-table.test.tsx`, `departments.test.tsx`), so only Step 3 adds assertions |
| The sheet's Radix title requirement is missed, triggering an a11y console error | Medium | Console noise, a11y regression | Step 7 explicitly requires `SheetTitle`; Step 9 asserts the drawer content renders, which exercises it |
| `NAV_ITEMS` extraction silently changes desktop nav behaviour | Low | Nav regression | Step 9 case 4 pins `MainNav`'s four hrefs after the extraction |
| The `w-full` filter change alters query behaviour (e.g. accidental re-render typing loops) | Low | Broken filters | Step 5 touches classes only; the 422-regression test ("clearing a filter drops the parameter instead of sending null") must pass unchanged |
| Two-column → one-column dialogs change the DOM order tests rely on | Low | Red suite | The change is purely CSS (`grid-cols-*`); field order and labels are untouched |
| `check-e2e` cannot run without Docker/Postgres | Medium | Gate not fully verified | `make db-up db-migrate db-seed` first (Makefile's own prerequisites); if Docker is unavailable, report `check-web` results and state that `check-e2e` is outstanding |

---

## Testing Strategy

- **Unit / component (Vitest + Testing Library)**:
  - **Add**: `apps/web/tests/layout/mobile-nav.test.tsx` — 4 cases (Step 9).
  - **Add**: one "names and hints the horizontal scroll region" case each in
    `tests/employees/employees-table.test.tsx`, `tests/organizations/{departments,positions,locations}.test.tsx`
    (Step 3) — 2 assertions per case (region name + hint text).
  - **Must keep passing unchanged**: every existing case in
    `tests/attendance/*`, `tests/employees/*`, `tests/organizations/*` — especially
    the 422-regression filter case, the 409 duplicate-email / name-taken field-error
    cases, the archive/restore cases (queried by `aria-label`), and
    `tests/attendance/attendance-records.test.tsx:64-71` / `shifts-panel.test.tsx:65-69`
    (the pinned scroll-affordance strings).
- **Integration / E2E**: none added (Non-Goal). The existing Playwright suite must
  still pass via `make check-e2e`.
- **Manual / QA checklist** (walk at 375px, 768px, 1024px, 1440px):
  - [ ] `/dashboard` — destination cards keep 10px radius + `shadow-sm`, hover raises to `shadow-md`.
  - [ ] `/employees` — table card is flat; "Swipe sideways…" hint visible at 375px only; six filters each fill a row at 375px and sit inline at 768px+; `EMP-00001` renders in Fira Code.
  - [ ] `/employees/[id]` — Edit/Transfer/Terminate unchanged; Profile and Current-assignment cards keep elevated chrome; employee code in Fira Code.
  - [ ] `/employees` → "New employee" — fields stack one per row at 375px with legible placeholders; two columns at 768px+; creating still toasts "Employee created".
  - [ ] `/attendance` — board header, check-in panel, counts, activity feed and records table all render at 375px; the "New activity" aside stacks below the feed; the date input and "Today" button stay reachable.
  - [ ] `/attendance` → "New shift" — start/end time stack at 375px.
  - [ ] `/settings/organization/{departments,positions,locations}` — flat table cards; scroll hint at 375px; Edit/Archive/Restore are 44px tall at 375px and 28px at 1024px; timezone column in Fira Code.
  - [ ] Header at 375px — one row only (brand, menu, theme toggle), no horizontal scroll; drawer opens from the right, highlights the active route, closes on selection and on Escape.
  - [ ] Header at 768px+ — inline nav, no menu trigger.
  - [ ] Dark mode — spot-check all of the above; borders/dividers remain visible and text keeps ≥ 4.5:1 (do not assume light-mode values carry).
  - [ ] Keyboard — tab through a list table at 375px and confirm the scroll region is focusable and scrolls; focus rings visible throughout.
  - [ ] Reduced motion — enable the OS setting; the sheet opens/closes without animation.
- **Regression checks**: `make check` from the repo root must pass in full.

---

## Rollout & Migration

- **Feature flag**: none — presentation-only, ships unconditionally. The kill-switch is
  `git revert` of the step commits (each is independently revertable).
- **Backwards compatibility**: no API, route, or data-shape change. Old URLs and all
  existing flows work identically; only presentation and the mobile shell change.
  The `sm:hidden` hint copy is new, additive UI copy and is not pinned by any
  `.ai/features/*.md` acceptance criterion (verified — no spec references it).
- **Data migration**: none (no schema, no data, no cache invalidation).
- **Rollback steps**: revert the commits in reverse order. Step 1 (font) and Step 7
  (sheet primitive) are the only self-contained additions; Step 8 depends on Step 7,
  so revert 8 before 7. No cache flush, no migration down.
- **Monitoring**: no runtime metrics apply. Watch only for build/typecheck regressions
  and for any Playwright e2e flakes after the shell change (the e2e suite may select
  the header nav — if a spec targets the removed `md:hidden` row, update that spec's
  selector to the drawer trigger in the same change).
- **Stakeholders to notify**: whoever owns `.ai/features/attendance.md`,
  `.ai/features/employees.md` and `.ai/features/organizations.md` — the presentation
  rules recorded in Step 10 apply to all future UI work in those features.

---

## Open Questions

- [ ] **Fira Code weight set.** This plan loads `400, 500, 600` to mirror the Fira Sans
  load. If the team prefers a lighter payload, `500` alone is enough for the data-label
  role — **default if unanswered: `400, 500, 600`** (matches the sibling font and costs
  three static weights).
- [ ] **Drawer side.** This plan uses `side="right"` (Decision 4). If the team prefers
  the iOS/Android convention of a left-side nav drawer, change one prop in
  `mobile-nav.tsx` — **default if unanswered: `right`**.
- [ ] **Hint copy wording.** "Swipe sideways to see every … field." is pinned by tests
  and matches the attendance exemplar. If product wants different wording, change it in
  the four components and their four tests together — **default if unanswered: keep the
  exemplar wording**.

---

## References

- `.ai/decisions/0005-frontend-stack.md` — frontend stack rationale
- `.ai/conventions.md` `## TypeScript — apps/web` (lines 48-91) — web conventions, incl. the UI-quality skill mandate
- `apps/web/AGENTS.md` — web-local rules and commands
- root `AGENTS.md` rule 15 — the `ui-ux-pro-max` / `frontend-design` / `shadcn` skill mandate for UI work
- `apps/web/app/globals.css` — the token set (light + dark) and the `@theme inline` bridge
- `apps/web/components/ui/table.tsx:11-27` — the `containerLabel` contract
- `apps/web/components/ui/button.tsx:22-31` — the size variants (`xs` = `h-6`)
- `apps/web/components/ui/card.tsx:8-16` — the elevated default
- `apps/web/components/ui/dialog.tsx` — primitive conventions the sheet must mirror (`:46` centered positioning is why a sheet is needed)
- `apps/web/features/attendance/components/AttendanceRecordsTable.tsx:162-167` — the table affordance exemplar
- `apps/web/features/attendance/components/ShiftsPanel.tsx:157-158` — the touch-target exemplar
- `apps/web/components/destinations.ts` — the navigation catalog
- `apps/web/tests/attendance/attendance-records.test.tsx:64-71` — the pinned-affordance test exemplar
- Makefile:39-51 — `check` / `check-web` / `check-e2e` gates
