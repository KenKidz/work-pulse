---
name: workpulse-ui
description: WorkPulse product-UI rulebook for apps/web — dashboards, list tables, forms, dialogs and live ops surfaces (command center). Use for any component, page, styling, accessibility or UI-review work in this repo; it is the only UI skill required for product work. Distills ui-ux-pro-max, frontend-design, design-taste-frontend-v1 and web-design-guidelines into one project-grounded rule set, says when to escalate to an upstream skill, and lists the upstream mandates that deliberately do NOT apply here.
---

# WorkPulse UI

One rulebook for product UI in `apps/web/`, and the **only UI skill required**
for it (root `AGENTS.md` rule 15). It distills four upstream skills
(`ui-ux-pro-max`, `frontend-design`, `design-taste-frontend-v1`,
`web-design-guidelines`) into rules that fit **this** codebase — so those are
not loaded for routine work — and says when to escalate to one of them.

The audience decides everything below: managers, HR and admins reading dense
operational data for long stretches, sometimes on a wall display. Not a
marketing visitor. Calm, legible, trustworthy, fast — not striking.

Authoritative siblings — this skill never overrides them:

- `.ai/conventions.md` → `Presentation rules` — source of truth for metrics.
- `apps/web/AGENTS.md` — layout, commands, stack rules.
- Root `AGENTS.md` rule 15 — which skills to load.
- `.ai/security.md` — no PII in copy, logs, fixtures or test ids.

## Scope and routing

| Work | Load |
| --- | --- |
| Any component / page / styling in `apps/web/` | this skill only |
| `components/ui/` primitives (add, fix, compose) | + `shadcn` |
| A concern this skill does not cover (a specific WCAG criterion, chart choice, stack performance) | + one targeted `ui-ux-pro-max` search — not the whole skill |
| Genuinely new art direction for a new surface | + `frontend-design` |
| Formal UI review / accessibility audit | + `web-design-guidelines` and `ui-ux-pro-max --domain ux` |
| Marketing / landing / public page | `design-taste-frontend` (v2) — this skill does not apply |

Landing pages, portfolios, heroes and scroll-telling are out of scope here —
`design-taste-frontend` owns them and itself excludes dashboards and data
tables. The two are complementary, never interchangeable.

Escalation searches — `ui-ux-pro-max`'s own SKILL.md prints a stale
`~/.kilocode/...` path; the real one on this machine is:

```bash
python3 ~/.kilo/skills/ui-ux-pro-max/scripts/search.py "error summary validation" --domain ux
python3 ~/.kilo/skills/ui-ux-pro-max/scripts/search.py "sticky header scroll inset" --stack nextjs
python3 ~/.kilo/skills/ui-ux-pro-max/scripts/search.py "dialog focus trap scrim" --stack shadcn
```

Query contract: one dominant intent, 2–5 terms, plus a constraint. Search the
**semantic outcome** first, then the stack for implementation. Retry once
narrower; verify the returned domain and top result actually fit; never persist
unverified output. Ignore its native-app sections (safe areas, Dynamic Type,
44pt/48dp) — this is desktop and mobile web.

## Effective dials for this product

Upstream skills parameterise design as `VARIANCE / MOTION / DENSITY`. For
WorkPulse product UI the values are fixed by the audience, not by taste:

- **VARIANCE 2–4** — predictable grids, symmetric panels, one shell width. No
  asymmetric hero compositions, no scroll-pinned sections, no masonry.
- **MOTION 1–3** — `:hover` / `:active` / focus transitions, one page-load
  moment, and motion that carries live-data meaning. Nothing loops for effect.
- **DENSITY 6–8** — boards and tables are cockpit-grade: 1px dividers, compact
  cells, mono numerals, metrics unboxed unless elevation earns its place.

## 1. Design identity (decided — extend, do not reinvent)

From `apps/web/app/globals.css`:

- **Metaphor:** clear paper, deep navy ink, one electric blue for operational
  focus. Light `#f4f7fb` / `#172b4d` / `#2563eb`; dark `#111b2e` / `#e8eef9` /
  `#7aa7ff`.
- **One accent, one palette.** The blue is the only accent. No second hue
  competing for attention, no purple or violet glows, no neon, no gradient text,
  no mixing warm and cool neutrals. Neutrals stay cool paper/navy throughout.
- **No pure black, no pure white.** Ink is `#172b4d`, dark background `#111b2e`.
- **Type:** Fira Sans (`--font-sans`) for everything read; Fira Code
  (`--font-mono`) only for data. **Sans-only on operational surfaces — no
  serif.** Two families, clearly distinct, self-hosted via `next/font`.
- **Radius:** one scale from `--radius: 0.375rem`. No ad-hoc radii, no
  `rounded-[2.5rem]`.
- **Semantic status tokens:** `success` / `warning` / `info` / `destructive`
  plus `-foreground`, deliberately darkened so 12–14px text keeps AA (4.5:1) on
  their own 5–15% tints, not just on white. Never hardcode emerald/amber/red
  hexes for status; if a fixed pair is truly required, comment why the token
  could not carry it.
- **Chart ramp:** `--chart-1..5` for any data viz.
- **Theme:** `.dark` class + `next-themes` + `components/theme-toggle.tsx`. Both
  themes are first-class; work is not done until checked in both.
- **Scrollbars are global:** one thin 8px treatment in `globals.css` — a 4px
  `--input` pill thumb (≈3:1 in both themes), transparent track,
  `--muted-foreground` on hover. Never restyle scrollbars per component.
- **Restraint:** spend boldness in one place per surface, keep the rest quiet.
  Before finishing, remove one accessory.

## 2. Layout, spacing and viewport

Verified shell facts — reuse them, do not invent parallel ones:

- **Shell:** `app/(dashboard)/layout.tsx` → `min-h-dvh` flex column, sticky
  header `h-16` at `z-40`, content `mx-auto w-full max-w-7xl px-4 sm:px-6 py-7
  sm:py-9`. Auth shell centres `max-w-sm` on `min-h-dvh`.
- **Viewport stability:** always `min-h-dvh`, never `h-screen` (iOS Safari
  address bar causes layout jump).
- **Grid over flex-math:** never `w-[calc(33%-1rem)]`; use
  `grid grid-cols-1 md:grid-cols-3 gap-…`.
- **Mobile collapse is explicit per section:** any multi-column layout declares
  its `<768px` fallback in the same component (`w-full`, `px-4`). No "Tailwind
  handles it" assumptions. The page itself never scrolls horizontally; only a
  table's own container may.
- **Scroll insets:** content must never hide behind the sticky `h-16` header or
  the mobile nav. Add bottom/top insets where a list runs under fixed chrome.
- **Spacing tiers already in use:** header `h-16`, main `py-7 sm:py-9`, page
  header `gap-2`, table head `h-10`, cell `p-2`. Extend this ladder; do not add
  one-off values.
- **Adaptive gutters:** horizontal insets grow with width (`px-4 sm:px-6`).
- **Z-index is a three-tier scale, nothing else:** `z-10` local stacking inside
  a component (timeline dot, avatar badge) · `z-40` sticky app chrome ·
  `z-50` Radix portals/overlays (dialog, sheet, select, dropdown). Never
  `z-[60]`, never an arbitrary value to "win" a fight — fix the stacking
  context instead.

## 3. Cards, surfaces and density

- **Card chrome by role** (`.ai/conventions.md`): data panels (table, count
  board, activity feed, check-in/shifts panel) are
  `rounded-md border-border shadow-none`; elevated cards (destination/link,
  landing, reading) keep `card.tsx` defaults (`rounded-xl shadow-sm`).
- **Dashboard hardening:** at this density, a nested card box is usually wrong.
  Group with `divide-y`, `border-t` or plain negative space; let metrics breathe
  unboxed unless elevation communicates real hierarchy.
- **Shadows are tinted to the background hue,** never pure black on light
  surfaces, and never used as decoration.
- **Borders and dividers must be visible in both themes** — check dark mode, not
  just light.

## 4. Typography

- **Page titles come from `components/page-header.tsx`**: `h1` at
  `text-3xl leading-9 font-semibold tracking-tight text-balance`, optional
  description `max-w-2xl text-sm leading-5 text-pretty text-muted-foreground`.
  It is server-safe — keep it that way. **`text-3xl` is the ceiling**; a page
  heading must not scream. Build hierarchy with weight and color, not scale.
- **Body measure:** keep reading width bounded (`max-w-2xl` for descriptions,
  ~65ch for prose). Use `text-muted-foreground`, never a raw gray utility.
- **Mono is for data:** employee codes, ids, timezones, clock times, counts.
  Numbers that change live also get `tabular-nums` so updates do not shift
  layout — CLS on an ops board is a defect.
- **No decorative type devices:** no all-caps eyebrow labels above sections, no
  accenting a single word in a heading, no typographic labels that repeat what
  position already tells the user, no numbered markers unless the content really
  is a sequence.
- **Structural devices carry information.** A divider, border or count is
  encoding something; if it encodes nothing, remove it.

## 5. Controls and components

Authoritative list with exemplars: `.ai/conventions.md` → `Presentation rules`.

- **Every control comes from `components/ui/`** — never raw
  `<button>/<input>/<select>/<textarea>`, never a feature-local copy of control
  metrics. Shared spec: `h-9`, `rounded-md`, `text-sm`, `border-input shadow-xs`
  fields, one focus ring `ring-[3px] ring-ring/50`,
  `aria-invalid:border-destructive`, `disabled:pointer-events-none
  disabled:opacity-50`.
- **Call-site `className` is layout-only:** pick `variant` + `size`; className
  may add width/spacing/alignment (`w-full sm:w-48`, `ml-auto`, `tabular-nums`)
  — never `h-*`, `size-*`, `rounded-*`, `text-*`, `border-*`, shadow or ring.
- **Touch targets use the `11` unit (44px)** — the one allowed metric override:
  row actions `min-h-11 sm:min-h-7`, icon buttons `size="icon"` + `size-11`.
- **Customise shadcn through `components/ui/` and tokens, never at call sites.**
  The primitives are already owned and tuned (semantic `Badge` variants, cool
  paper tokens); a raw default-state component reaching a page is a defect.
- **Class merging** uses `cn` from the `cn` package (`import { cn } from "cn"`),
  not a local `lib/utils`. Conflicting utilities are merged away — but keep
  call sites layout-only so there is nothing to fight.
- **Icons: `lucide-react` only**, one family, consistent stroke; primitives size
  them via `[&>svg]:size-*`. Never hand-roll icon SVG paths, never add a second
  icon library without a spec.
- **No emoji** anywhere — not in markup, copy, alt text or tests. Use a glyph.
- **Forms:** label above input (never placeholder-as-label), helper text present
  in markup, error text below input, `gap-2` blocks, dialog field pairs
  `grid-cols-1 items-start gap-… sm:grid-cols-2`. **`items-start` is
  mandatory** — a description or error on only one field makes that
  `FormItem` taller, and without it the sibling stretches and its
  label/control stack shifts down out of line (overtime request dialog
  regression). Long dialogs `max-h-[85vh] overflow-y-auto`; width by content
  (`sm:max-w-md` confirm / `sm:max-w-lg` / `sm:max-w-xl` form).
- **Field hints are tooltips, not visible helper lines.** Explanatory copy
  about one field goes on the label as `hint`
  (`<FormLabel hint="Hiring stops once this many candidates are hired.">`),
  which renders a 24×24 info trigger beside the label and a tooltip on
  hover/focus. Do not render it as an always-visible `FormDescription` line:
  it lengthens every dialog, competes with the input and drifts the label
  row. `FormMessage` stays inline below the control. Dialog-level intros stay
  in `DialogDescription`, and guidance that governs several fields stays
  visible at group level. The trigger is a real `type="button"` with an
  accessible name, carries a 24×24 target (WCAG 2.2) and must not change the
  label row height; the hint text stays in the DOM as the control's
  `aria-describedby` description, so it is available without opening the
  tooltip.
- **List tables:** pass `containerLabel` to `components/ui/table.tsx` — it adds
  `role="region"`, `aria-label`, `tabIndex={0}` and a visible focus outline, so
  the swipe-only affordance has a keyboard equivalent. Render the `sm:hidden`
  "Swipe sideways to see every … field." hint above it
  (`AttendanceRecordsTable.tsx:162-167`). Surfaces that stack rows on mobile
  instead (`realtime/components/PresenceTable.tsx`) drop the hint. Table
  language: head `h-10 px-2 whitespace-nowrap`, cell `p-2 whitespace-nowrap`,
  row `border-b hover:bg-muted/50`, footer `border-t bg-muted/50`.
- **Filters** stack below `sm` (`w-full sm:w-…`) and are debounced ~300ms
  (`hooks/use-debounced-value.ts`); discrete controls apply immediately.
- **Prefer existing abstractions:** `PageHeader`, `EmptyState`, `Badge`, `Alert`,
  `Spinner`, `main-nav` / `mobile-nav` + `destinations.ts`. No component built
  "for later".
- **Dependency verification is mandatory:** before importing anything, check
  `apps/web/package.json`. pnpm's strict `node_modules` means every imported
  package must be declared there — no relying on hoisted transitive deps. A new
  dependency needs a feature spec justifying it (root `AGENTS.md` 10–11).
- **Tailwind v4 guard:** `postcss.config.mjs` uses `@tailwindcss/postcss`. Never
  add the `tailwindcss` PostCSS plugin, never write v3-only syntax.

## 6. Data and status semantics

- **Wire values are never rendered.** Map API enums to labels in the feature's
  `labels.ts` (`present` → "Present") so a schema change cannot leak into copy.
- **Status uses `Badge` variants** (`success`, `warning`, `destructive`,
  `muted`, `secondary`, `outline`) — the tinted chips already hold AA contrast
  in both themes. Do not hand-roll chips.
- **Time renders through `lib/datetime.ts`** (dayjs, UTC in / local out). Never
  `new Date(string)` arithmetic, never raw ISO in the UI.
- **Color is never the only indicator** — a status carries a label or icon too.
  A dot that conveys real state (live presence) is allowed; a decorative dot is
  not.
- **Real data or nothing.** Seeds and fixtures are deterministic and synthetic
  (`.ai/testing.md`) — believable, never "John Doe" / "Acme" / "Jane Doe",
  never real people, never invented precision (`99.99%`, `4.1×`) presented as
  fact.

## 7. States are part of the design

Every data-fetching surface ships all of these; they are not follow-up work:

- **Loading:** a stable-height placeholder matching the final shape (no layout
  shift). `Spinner` is for short inline or transient waits, not for the first
  paint of a large panel.
- **Empty:** composed via `components/empty-state.tsx`, saying what belongs
  there and how to populate it. An empty screen is an invitation, not a dead
  end.
- **Error:** `ApiError.code` mapped to a user-visible message; transient
  mutation failures via `sonner` toast; persistent read failures inline with a
  retry.
- **Denied:** redirect plus a short notice (mirrors `UsersTable`). The UI only
  hides; `policies.py` decides.
- **Disabled means disabled:** real `disabled` semantics, reduced emphasis, no
  action — never a control that looks pressable and does nothing.

## 8. Live and realtime surfaces

`features/realtime/` is the reference implementation:

- **Snapshot first, then patches.** The REST snapshot renders the board;
  WebSocket frames land in the Query cache via `setQueryData` — never a shadow
  copy in component state or Zustand. Server data lives only in the cache.
- **Connection state is a visible tri-state** (`LiveStatusBanner.tsx`):
  `connecting` (spinner + "latest snapshot meanwhile"), `live` (pulse + last
  update), unavailable (destructive `Alert` keeping data on screen and stating
  the refresh cadence). A degraded board never blanks out.
- **Scope live regions tightly.** `role="status"` wraps the state chip only; a
  region that re-announces on every board patch is unusable with a screen
  reader. Comment the reason, as that file does.
- **Live motion is semantic.** `animate-ping` means "socket connected", and only
  the animated layer is hidden under reduced motion (`motion-reduce:hidden`)
  while the static dot remains.
- **Scroll regions are keyboard-reachable:** `tabIndex={0}` + `aria-label` +
  visible focus ring (built into `table.tsx`; apply the same to custom rails).
- **Bound uncapped live panes.** A board with one row per employee (presence
  rows, the activity feed) must scroll inside a fixed height — `max-h-[min(60dvh,
  36rem)] overflow-y-auto` — never grow the page one row per employee. A
  bounded `Table` pairs `containerClassName` with `TableHeader sticky` so the
  column labels survive the scroll (`PresenceTable.tsx` is the exemplar).
- **Effects clean up.** Sockets, timers and observers are torn down in the
  `useEffect` cleanup — a wall display runs for hours.
- **Never drive continuous values through `useState`.** Mouse position, scroll
  progress or animated values re-render the tree every frame; if such a value is
  ever needed, it requires a motion library and therefore a spec.
- **Auto-refreshing content must not steal focus or scroll position,** and
  anything auto-rotating needs a pause control.

## 9. Motion

- Animate `transform` and `opacity` only — never `top/left/width/height`.
  `will-change` sparingly, only on elements that actually animate.
- Every animation must answer "what does this communicate?": hierarchy,
  feedback, state transition, or live-data meaning. If it cannot be justified in
  one sentence, cut it.
- **One orchestrated moment per surface.** The single page-load animation in the
  app is the login card's pulse trace (`globals.css`). Scattered
  fade-and-slide-up entrances on every panel read as generated noise.
- No infinite loops on data cards, no shimmer-for-ever, no scroll-jacking, no
  parallax, no magnetic physics, no glassmorphism on operational surfaces.
- The global reduced-motion block lives in `globals.css`; still add
  `motion-reduce:` where an element must disappear rather than merely stop.
- Never `window.addEventListener("scroll", …)`; use `IntersectionObserver` or
  CSS scroll-driven animations if a scroll effect is genuinely needed.
- No GSAP / Motion / Three.js — none are in `apps/web/package.json`, and adding
  one needs a spec.
- Texture and grain (if ever used) go only on fixed `pointer-events-none`
  layers, never on scrolling containers — continuous GPU repaints destroy mobile
  frame rates.

## 10. Accessibility floor

Verify before declaring UI work done:

- Visible focus on every interactive element; DOM order matches visual order;
  sticky chrome never obscures keyboard focus.
- Icon buttons and icon-only controls have an accessible name; decorative icons
  beside visible text are `aria-hidden="true"`; meaningful standalone icons have
  a text alternative; controls expose `selected` / `pressed` / `expanded`.
- Meaningful icons and control boundaries keep ≥ 3:1 against adjacent colors.
- Contrast ≥ 4.5:1 body text, ≥ 3:1 large text — **verified in both themes**,
  including text on tinted status backgrounds (that is why the tokens are
  darkened). Check dark mode independently; never assume light values carry.
- Hover, focus and disabled states are equally distinguishable in both themes.
- Dialog/sheet scrim (`bg-black/50`) is judged against the real composed
  background, not in isolation; foreground stays legible.
- Forms: failed submit keeps inline field errors; with multiple errors, focus a
  linked error summary. No placeholder-as-label.
- Reduced motion and 200% zoom produce no broken layout or clipped text.
- Swipe-only and drag-only interactions have a keyboard or button alternative
  (that is what `containerLabel` buys).
- Test at 375px, 768px, 1440px and landscape.
- No PII in copy, `console.*`, error messages, `data-testid` values or
  analytics.

For an audit pass: load `web-design-guidelines` (it fetches the current Web
Interface Guidelines and reports terse `file:line` findings), then run
`ui-ux-pro-max --domain ux` searches per concern — one observable outcome per
query. Do not accept a generic result for a specific WCAG criterion.

## 11. Copy

- Name things by what users understand, not by how the system is built
  ("notifications", not "webhook config").
- Active voice. A CTA says exactly what happens ("Save changes", not "Submit"),
  and an action keeps one name through the whole flow — the button that says
  "Publish" produces a toast that says "Published".
- Sentence case, plain verbs, no filler: never "Elevate", "Seamless",
  "Unleash", "Next-Gen", "Revolutionize".
- Errors explain what went wrong and what to do next, in the interface's voice.
  They do not apologize and are never vague.
- One job per string; one register per surface. Cut anything that does not help
  someone act.

## 12. Anti-slop tells (banned in this repo)

AI-purple or neon glows · pure `#000000` / `#ffffff` · oversaturated accents ·
gradient text on headings · custom mouse cursors · oversized page headings ·
serif on operational surfaces · emoji as icons · hand-rolled icon paths · a
second icon or component library · three identical marketing feature cards ·
decorative eyebrows and all-caps micro-labels · generic placeholder names and
fake-precise numbers in shipped UI · raw default-state shadcn components ·
hardcoded status hexes · one-off radii, z-indexes or spacing values · cards
nested inside cards · infinite decorative animation · `h-screen` ·
`window.addEventListener("scroll")`.

## 13. Upstream mandates that do NOT apply

Recorded so they are not re-applied by accident:

| Upstream rule | Why it does not apply |
| --- | --- |
| "Every card must loop infinitely so the dashboard feels alive" (v1) | Ops surfaces are watched for hours; perpetual motion distracts, breaks reduced-motion intent and burns GPU on wall displays. |
| Fixed dials `VARIANCE 8 / MOTION 6 / DENSITY 4` (v1) | Replaced by this project's 2–4 / 1–3 / 6–8 (see Effective dials). |
| Bento 2.0 palette: `#f9fafb`, white cards, `rounded-[2.5rem]`, titles below cards (v1) | Conflicts with the project tokens and card-chrome-by-role rule. |
| Icons must be Phosphor or Radix; Lucide discouraged (v1, `ui-ux-pro-max`) | The stack is shadcn/ui + `lucide-react` (ADR 0005). One family; a second needs a spec. |
| "Inter banned — use Geist / Satoshi / Cabinet Grotesk" (v1) | Type is chosen and self-hosted: Fira Sans + Fira Code. |
| `text-gray-600` body defaults (v1) | Raw palette utilities bypass tokens; use `text-muted-foreground`. |
| Anti-center bias, hero discipline, asymmetric heroes (v1/v2) | Product pages have no hero; `PageHeader` is left-aligned. |
| Absolute em-dash ban (v2) | Not a repo rule; prose in docs uses them. UI copy follows §11 instead. |
| Mandatory image generation, logo walls, social proof (v2) | Marketing-only; never in an authenticated ops surface. |
| Liquid glass, magnetic physics, gooey menus, Three.js (v1/v2) | Not in the dependency set and not appropriate for operational UI. |
| Safe areas, Dynamic Type, 44pt/48dp (`ui-ux-pro-max` native sections) | Web only — the `11` unit (44px) covers touch targets. |

Adopted from upstream: dependency verification, RSC/client-leaf isolation,
`min-h-dvh`, grid-over-flex-math, dashboard hardening and anti-card-overuse,
full interaction cycles, z-index restraint, mono numerals at high density, AI
tell list, Jane Doe / fake-number bans (v1); subject-grounded direction,
restraint, structural-device discipline, copy rules, self-critique with
screenshots (`frontend-design`); semantic tokens, contrast and theme parity,
scrim legibility, icon semantics, focus and form guidance, search query contract
(`ui-ux-pro-max`); guideline-based review with `file:line` findings
(`web-design-guidelines`).

## 14. Candidate upgrades (not in the codebase yet)

Good upstream ideas that would change shared primitives — each needs the
`shadcn` skill, a `.ai/conventions.md` entry, and tests. Do not apply silently:

- **Tactile press feedback** on `components/ui/button.tsx`
  (`active:scale-[0.98]` or `active:-translate-y-px`, `motion-reduce:` exempt).
  Buttons currently give hover + focus feedback only; a press confirmation helps
  touch use on the floor.
- **A skeleton primitive** for first paint of large panels, replacing
  stable-height placeholders, if loading states ever feel slow.

## 15. Pre-flight

- [ ] Controls, cards, tables, filters, dialogs and touch targets follow
      `.ai/conventions.md` → `Presentation rules`; call-site `className` is
      layout-only.
- [ ] Only semantic tokens; one accent; radius/spacing/z-index from the existing
      scales; no hardcoded status hexes.
- [ ] Wire values mapped to labels; times via `lib/datetime.ts`; live numbers
      `font-mono tabular-nums`.
- [ ] Loading / empty / error (/ denied) states all present, no layout shift.
- [ ] Accessibility floor (§10) checked in **both** themes.
- [ ] Reduced motion verified; nothing loops infinitely; effects clean up.
- [ ] Responsive at 375 / 768 / 1440 and landscape; `min-h-dvh`; no horizontal
      page scroll; multi-column sections declare their mobile collapse.
- [ ] Copy re-read: no filler, no invented numbers, no PII, CTAs say what they
      do, errors say what to do next.
- [ ] No new dependency, no duplicated primitive, no component built "for
      later"; every import verified against `apps/web/package.json`.
- [ ] Screenshots reviewed for any visual change — a picture beats a
      description.

## 16. Verify

```bash
# from apps/web
pnpm run typecheck && pnpm run lint && pnpm run test
# full gate from the repo root (migrate + seed + dev servers + e2e)
make check
```

Visual check: `make db-up && make db-migrate && make db-seed`, then
`make dev-api` (:8000) and `make dev-web` (:3000). Log in with a seeded account
from `database/postgres/seeds/003_users.sql` and inspect both themes.
