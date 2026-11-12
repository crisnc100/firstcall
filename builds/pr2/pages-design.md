# Firstcall PR2: browser pages design brief

For the builder of the PR2 pages in `web/`. It covers what the pages look like and how they
behave. The API is already built (`firstcall/board/routes.py`); nothing here changes it.

The visual reference is `design/comp.html`: open it in a browser, resize it, and switch the
OS theme. Screenshots of it are in `design/` too. Where the comp and this brief disagree, the
brief wins.

## 1. Who uses it, and what they need

- **Salesperson.** Usually on a phone, often between other tasks. They hear a push and open
  the pool. In under ten seconds they need to see what came in, how long it has waited, and
  claim it with one tap. Losing a claim to a teammate is normal, not an error.
- **Team lead.** Phone or desktop. Watches their team's pool and owned leads, reassigns and
  returns.
- **Monitor.** Mostly desktop, at a desk for a shift. Watches Unassigned, places leads into
  teams, checks the Not-a-lead tray for real leads filed wrongly, and sees who has waited too long.
- **Admin.** Desktop. Adds users, sets roles, teams and passwords.

This is a working tool (impeccable "Operate" mode): the tool should disappear into the task.
Familiar controls, no decoration, precision in the details.

## 2. Direction: "the dispatch board"

1. Time is the main thing on the screen. Every unclaimed lead leads with how long it has waited, in large, steady numerals.
2. Calm by default: warm paper neutrals, dark ink, one blue for actions. Colour appears only when a lead is getting late.
3. Urgency rises in four steps (New, Waiting, Late, Very late). Each step adds a word and an icon, not only a colour.
4. Lists, not card grids: one lead per row, scannable top to bottom, oldest first, with the action at the row's end.
5. Phone first for the pool and my leads (thumb-sized Claim, bottom tab bar); desktop gets a top bar and denser rows.
6. Plain words everywhere: "Claim", "Release to pool", "Already claimed". No jargon, no exclamation marks, no cheerleading.

Light and dark are both first-class and follow the device setting. Reps use phones outdoors
and in cars (light); monitors sit at desks through evening shifts (often dark).

## 3. Tokens

Put these in one `web/src/styles/tokens.css` as CSS custom properties. Every colour in the app
comes from a token; no hex values in component CSS.

### Colour

All text pairs below were measured (WCAG 2 contrast). Body text is at least 6:1, every
coloured label at least 4.7:1, and input borders at least 3:1 against their surface.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--bg` | `#F6F5F1` | `#121311` | Page background |
| `--surface` | `#FFFFFF` | `#1B1C19` | Rows, panels, top bar |
| `--surface-sunken` | `#EEECE6` | `#0D0E0C` | Message text blocks, table header, skeletons |
| `--border` | `#DAD7CE` | `#2E302B` | Row dividers, panel edges (decorative) |
| `--border-strong` | `#8C887D` | `#6E7168` | Input and select borders (3.5:1) |
| `--text` | `#1C1B18` | `#ECEBE6` | Body text, names, timers (15.8:1 / 15.6:1) |
| `--text-muted` | `#5C5950` | `#A6A49B` | Secondary lines, labels (6.4:1 / 6.9:1) |
| `--accent` | `#2747C9` | `#8EA2FF` | Primary buttons, links, focus ring, selected tab |
| `--on-accent` | `#FFFFFF` | `#0E1336` | Text on primary buttons (7.4:1 / 7.5:1) |
| `--accent-hover` | `#1F3AA8` | `#A9B8FF` | Primary button hover |
| `--accent-tint` | `#E8ECFB` | `#1E2440` | Selected row, active tab background |
| `--waiting-fg` / `--waiting-bg` | `#8A5A00` / `#FBF0D9` | `#F2C063` / `#2E2410` | Urgency: Waiting (5.2:1 / 9.1:1) |
| `--late-fg` / `--late-bg` | `#B4410E` / `#FCE6DA` | `#FF9A66` / `#33190D` | Urgency: Late (4.7:1 / 7.8:1) |
| `--verylate-fg` / `--verylate-bg` | `#A3161B` / `#FBE1E1` | `#FF8A8A` / `#3A1214` | Urgency: Very late, tinted (6.3:1 / 7.3:1) |
| `--verylate-solid` / `--on-verylate` | `#A3161B` / `#FFFFFF` | `#FF8A8A` / `#2A0507` | Very late badge, solid (7.8:1 / 8.2:1) |
| `--danger` | `#B42318` | `#FF8A80` | Error text, destructive buttons (6.6:1 / 7.5:1) |
| `--success` | `#1F7A3A` | `#7BD69A` | "Claimed", "Placed", push on (5.4:1 / 9.7:1) |

Dark mode: `@media (prefers-color-scheme: dark)` on `:root`. No theme toggle in v0.1.

Also theme the parts the browser draws: `::selection` (accent-tint bg, text fg),
`accent-color: var(--accent)` for checkboxes, `color-scheme: light dark` on `:root`, and
scrollbars via `scrollbar-color: var(--border-strong) transparent`.

### Type

- **One family:** Atkinson Hyperlegible Next (Braille Institute, SIL Open Font License),
  variable weight. Built for legibility at small sizes and at a glance, which is the job here.
  Self-host the woff2 files in `web/public/fonts/` with `font-display: swap`. Fallback stack:
  `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`. No Google Fonts link, no CDN.
  (The exact npm/fontsource package name is unverified; downloading the release woff2 is fine.)
- **Numerals:** the font draws a zero with an inner mark, so 0 and O never get confused in
  phone numbers. Keep it. `font-variant-numeric: tabular-nums` on every timer, count and time, so a
  ticking timer never jiggles.
- **Scale** (fixed rem, ratio ~1.2, never fluid):

| Token | Size / line height | Weight | Use |
|---|---|---|---|
| `--text-xs` | 0.75rem / 1rem | 500 | Badges, table header labels |
| `--text-sm` | 0.875rem / 1.25rem | 400 | Secondary lines, metadata |
| `--text-md` | 1rem / 1.5rem | 400 | Body, buttons, inputs (inputs never below 16px, so iOS does not zoom) |
| `--text-lg` | 1.125rem / 1.5rem | 600 | Lead name in a row |
| `--text-xl` | 1.375rem / 1.75rem | 600 | Page titles, timer in a row |
| `--text-2xl` | 1.75rem / 2.125rem | 650 | Lead name on its detail page |

Weights: 400 body, 600 names and buttons, 650 to 700 for page titles. No italics for emphasis;
no letter-spacing tweaks except `+0.02em` on the uppercase-free `--text-xs` badges.

### Space, shape, depth

- **Space scale (4px base):** `--s1` 4, `--s2` 8, `--s3` 12, `--s4` 16, `--s5` 24, `--s6` 32, `--s7` 48.
- **Page gutter:** 16px on phones, 24px from 768px, content max width 1120px centred.
- **Radius:** `--r-sm` 6px (badges, inputs), `--r-md` 10px (buttons, panels). No pill shapes except the inbox count.
- **Depth:** flat rows separated by 1px `--border`. One shadow token for things that float
  (toast, menu): `0 4px 16px rgb(0 0 0 / 0.12), 0 1px 3px rgb(0 0 0 / 0.08)`; in dark mode the
  floating surface is `#24251F` with the same shadow at higher opacity.
- **Touch targets:** every control at least 44 by 44px. The Claim button is 48px high on phones.
- **Focus:** `outline: 2px solid var(--accent); outline-offset: 2px` on `:focus-visible`, on
  every interactive element, including rows that are links. Never `outline: none` without this.
- **Motion:** one authored moment only, the claim (see 5.3). Everything else is instant.
  Respect `prefers-reduced-motion: reduce` by removing even that.

## 4. App shell and navigation

Signed-in pages share one shell. The nav items come from `GET /session` (roles and teams);
show only what the user can use, never a disabled item.

| Item | Shown to | Route |
|---|---|---|
| Pool | Anyone in a team | `/pool` (team picker inside) |
| My leads | Anyone in a team | `/mine` |
| Unassigned | Monitor, admin | `/unassigned` |
| Waiting longest | Monitor, admin | `/waiting` |
| Not-a-lead tray | Monitor, admin | `/tray` |
| Inbox | Everyone | `/inbox` (unread count) |
| Users | Admin | `/admin/users` |
| Alerts on this device | Everyone, in the account menu | `/alerts` |

Route form (history or hash) is the builder's call, but a reload on any route must work.

- **Desktop (≥ 768px):** a 56px top bar on `--surface` with a bottom border. Left: the word
  "Firstcall" in 600 weight (text, no logo mark). Then the nav items as text tabs; the current one
  in `--accent` with a 2px underline and `aria-current="page"`. Right: Inbox with its count, then
  the account menu (the user's name; opens a small menu: "Alerts on this device", "Sign out").
- **Phone (< 768px):** a 52px top bar with the page title and, at the right, the inbox icon
  with its count. A fixed bottom tab bar (64px plus the safe area inset) with up to four items,
  icon above a short label:
  - salesperson or team lead: Pool, My leads, Inbox, Account;
  - monitor: Unassigned, Waiting, Tray, Account;
  - someone who is both monitor and in a team: Pool, Unassigned, My leads, More; "More" lists the rest.
  Account holds Alerts on this device, Users (admin) and Sign out.
- **Icons:** one set, Lucide (MIT), inlined as SVG, 1.75px stroke, 20px in nav, 16px inline. No
  emoji or Unicode symbols as icons.
- **Skip link** "Skip to content" as the first focusable element.
- **Live updates:** no push of data to the page exists, so lists refresh every 15 seconds while
  the tab is visible and right away when it becomes visible again (`visibilitychange`). Pause
  when hidden. A refresh never moves keyboard focus or scrolls the list.
- **Clock skew:** timers use the server's time, not the phone's. Take the offset from each API
  response's `Date` header and apply it when computing "waited".

## 5. Components and their states

### 5.1 Wait timer (the core component)

Shows how long an unclaimed lead has waited since it arrived (`arrival_at`).

- **Format:** under 1 minute "Just now"; then "4 min", "59 min", "1 h 05 min", "23 h 40 min",
  then "2 d 3 h". Updates every 15 seconds on the client from the skew-corrected clock.
- **Re-entered the pool:** when `pool_since` is later than `arrival_at`, the timer still counts
  from arrival (the plan's rule: the first figures stand) and a second line says
  "Back in pool 6 min ago".
- **Urgency levels** (wall-clock minutes since arrival; see Assumptions):

| Level | Waited | Look | Word and icon |
|---|---|---|---|
| New | under 5 min | Timer in `--text`, no fill | "New", no icon |
| Waiting | 5 to 14 min | `--waiting-fg` timer, `--waiting-bg` badge | "Waiting", clock icon |
| Late | 15 to 59 min | `--late-fg` timer, `--late-bg` badge | "Late", alert-triangle icon |
| Very late | 60 min or more | `--verylate-fg` timer, solid `--verylate-solid` badge | "Very late", alert-octagon icon |

  The level is never colour alone: the badge always carries the word, and the icon shape
  changes. Screen readers hear one phrase, for example `aria-label="Waited 22 minutes. Late."`
  The visible timer itself is not an `aria-live` region (it would chatter every 15 seconds).
- Rows are never re-sorted by urgency on the client; the API's order (oldest arrival first)
  already puts the latest leads on top.

### 5.2 Lead row

One `<li>` per lead in a `<ul>` with an accessible name ("North pool, 7 leads").

Contents, in reading order:
1. Wait timer and urgency badge (pool views) or "Claimed 3 h ago" (my leads).
2. Name in `--text-lg`. No name: "No name given" in `--text-muted`.
3. One line: what they asked about (`item`), or "No details yet".
4. One muted line: team name · arrived time (local, "10:42" today, "Tue 10:42" this week, "12 Nov" older).
5. Notices when present: "Possible duplicate" (when `possible_duplicates` is non-empty) as a
   small neutral badge with a copy icon.
6. The row's action (Claim, Place) at the end.

The name is a link to the lead's detail; the whole row is not one giant link (the button must
stay a separate target). Phone and email are not shown in lists, only on the detail page.

**Phone layout:** timer and badge on the first line, at left, `--text-xl`; name and lines below;
Claim button at the right of the first line, 48px high, min 96px wide. Rows have 16px padding,
1px divider. The timer and badge never wrap (`white-space: nowrap`). In the already-claimed
state the "Already claimed by a teammate" text moves to its own full-width last line, since
it is too long for the button's spot.
**Desktop layout:** a grid row: timer column (128px), name and detail column (flexible), team
and arrival column (200px), action column (auto, right-aligned). Rows 64 to 72px high.

### 5.3 Claim button and claim outcomes

| State | What the user sees |
|---|---|
| Ready | Primary button "Claim" (`--accent`, `--on-accent`) |
| Working | Same size, label "Claiming…", spinner icon, `disabled`, `aria-busy="true"`. Other rows stay usable. |
| Won | The row's background flashes `--accent-tint` for 200ms, then the row collapses out (height to 0, 180ms, ease-out). A toast: "You own this lead now." with a link "Open lead". The lead appears in My leads. |
| Already claimed (409, detail "already claimed") | The row stays where it is, dimmed to `--text-muted`, the badge replaced by a neutral "Already claimed" badge with a lock icon, and the button replaced by plain text "Already claimed by a teammate". It stays until the next refresh removes it, and it never disappears while the user is looking at it unprompted for at least 5 seconds. Announce via the page's polite live region: "Already claimed by a teammate." |
| Other 409 ("the lead is not in a state that allows this") | Same treatment, text "This lead has moved on." |
| 404 | The row is removed; toast: "This lead is no longer in this pool." |
| Network or 5xx | Button returns to "Claim"; inline text under the row in `--danger`: "Couldn't claim. Check your connection and try again." |

Focus after a won or lost claim moves to the next row's Claim button (or the list heading if
none), so a keyboard user is never dropped on `body`.

The text "already claimed" must be visible on the page for the F29 Playwright check.

### 5.4 Buttons

- **Primary:** `--accent` fill, `--on-accent` text, 600 weight, `--r-md`, 44px (48px for Claim on phones). Hover `--accent-hover`. Pressed: 1px down, no colour change.
- **Secondary:** `--surface` fill, 1px `--border-strong`, `--text`.
- **Destructive:** secondary shape with `--danger` text and border (Return to pool, Deactivate). Never red fill.
- **Disabled:** 0.5 opacity plus `disabled`; never the only signal of why. Prefer hiding an action the API did not list.
- **Working:** label changes to the "…ing" verb, `aria-busy`, width does not change.

### 5.5 Inline panels (instead of modals)

Reassign, Move team, Place and Set password open an inline panel directly under the trigger
(phone: a full-width panel pushing content down; desktop: under the button in the action
column). It holds a labelled `<select>` and two buttons, the primary verb and "Cancel".
Focus moves to the select on open and back to the trigger on Cancel or Escape. No `<dialog>`
modals in PR2.

Confirmation is needed only for actions that take a lead from someone: Return to pool and
Reassign. The panel itself is the confirmation; its primary button repeats the full sentence
("Return to North pool").

### 5.6 Toast

Bottom centre on phones (above the tab bar), bottom right on desktop. One at a time; a new one
replaces the old. Stays 6 seconds, longer if it holds a link and has focus or hover. Lives in
an `aria-live="polite"` region. Never used for errors that need action; those stay inline.

### 5.7 Page states (every list page)

- **Loading (first load only):** three skeleton rows in `--surface-sunken`, no shimmer. Later
  refreshes are silent; if a refresh is slow, nothing changes on screen.
- **Empty:** short heading plus one line and, where it helps, one action. Copy in section 7.
- **Error (first load failed):** "Couldn't load the pool." plus "Try again" button. A failed
  background refresh shows a small muted line at the list top: "Not updated since 10:42. Retrying." and clears itself on success.
- **Signed out (401 anywhere):** go to sign in with the current route remembered, and show
  "You were signed out. Sign in to carry on."
- **CSRF refused (403):** fetch `GET /session` for a fresh token and retry the request once;
  if it fails again, show the generic error.

### 5.8 Badges

`--text-xs`, 500 weight, `--r-sm`, 2px by 8px padding, icon 12px plus word. Lead state words:
Pool "In pool", owned "Owned", nurture "Nurture", won "Won", lost "Lost", not_real "Not real".
Tray categories: "Vendor", "Support", "Spam". All neutral (`--surface-sunken`, `--text`)
except the urgency badges.

## 6. Pages

### 6.1 Sign in (`/signin`)

- Centred column, 360px max, on `--bg`, roughly a third down the viewport on desktop, top-aligned
  with 48px space on phones. "Firstcall" wordmark, then heading "Sign in".
- Fields: "Email" (`type=email`, `autocomplete=username`), "Password" (`type=password`,
  `autocomplete=current-password`, a "Show" toggle button inside the field). Button "Sign in", full width.
- Under it, muted: "Forgot your password? Ask your admin to set a new one."
- Gets a token from `GET /csrf` on load; sends it on POST.
- **States:** working "Signing in…"; 401 "That email and password don't match."; 429 "Too many
  tries. Wait a minute, then try again."; network "Can't reach Firstcall. Check your connection."
  Errors appear above the button in `--danger` with an icon, are linked with `aria-describedby`,
  and focus moves to the email field. Never say which of the two was wrong.
- After sign in: monitors with no team go to Unassigned; everyone else to Pool; a remembered route wins.

### 6.2 Team pool (`/pool`, `/pool/:teamId`)

- Heading: "North pool" with a count "7 waiting". With more than one team: a team picker
  under the heading, as text tabs on desktop (up to 5 teams; a `<select>` beyond that) and a
  `<select>` labelled "Team" on phones. The last chosen team is remembered per browser.
- List of lead rows (5.2) with Claim. Data: `GET /leads?view=pool&team_id=`.
- Team leads also see a second tab "Owned in North" (`view=team`, owned and nurture leads)
  with owner names, no Claim button. Salespeople do not see this tab.
- Empty: "No one is waiting in North." / "New leads appear here the moment they arrive."

### 6.3 My leads (`/mine`)

- Heading "My leads", count. `GET /leads?view=mine`. Rows show the state badge (Owned,
  Nurture), "Claimed 3 h ago", name, item, team. Sorted as the API returns them.
- Empty: "You don't own any leads yet." / "Claim one from your team's pool." with a "Go to pool" link.

### 6.4 Lead detail (`/leads/:id`)

Read-only, plus the buttons for the `actions` the API lists for this viewer. Show nothing
the API did not list.

- **Header:** back link ("Back to North pool" or "Back to My leads", from where they came;
  default to Pool). Name in `--text-2xl`. Under it: state badge, team, owner ("Owned by Ana
  Ruiz" or "No owner").
- **Wait or claim line:** in pool, the wait timer (5.1) at `--text-xl`. Owned: "Claimed in 12
  business minutes (18 min on the clock)" from `claim_business_minutes` and `claim_minutes`.
- **Contact:** phone as a `tel:` link, email as a `mailto:` link, each with a "Copy" button
  ("Copied" for 2 seconds). Missing: "No phone given", "No email given".
- **Facts** (a `<dl>`, two columns on desktop, one on phones): Asked about, Arrived (full local
  date and time), Source time (only when it differs from arrival by more than a minute, labelled
  "Sent at (per the message)"), Back in pool since (when relevant).
- **Notices:** flags and possible duplicates, as a neutral bordered panel with an info icon:
  "This may be the same buyer as another lead." Links to those leads only if they are visible to
  this viewer (a 404 means hide it).
- **Actions:** phone: a sticky bar at the bottom (above the tab bar) holding the primary action
  full width (Claim, or Release to pool for the owner) and a "More actions" button for the rest,
  which opens an inline list. Desktop: a right-hand column, 280px, sticky, with all actions as
  a vertical button list. Labels:
  - `claim` "Claim" (primary)
  - `release` "Release to pool"
  - `return` "Return to pool" (destructive style)
  - `reassign` "Reassign…" (panel: select from `GET /teams/{id}/members`, current owner excluded)
  - `move_team` "Move to another team…" (panel: select of teams from `GET /teams`, current excluded)
  - `place` "Place in a team…" (panel, same team select)
  After any action, the page reloads the lead from the API's answer and shows a toast ("Released
  to North pool."). A 409 shows the claim treatment ("Already claimed by a teammate"), inline in
  the action area.
- **Messages:** heading "Messages (3)". Oldest first. Each message: source and received time on
  one muted line, category and status badges, then the text in a `--surface-sunken` block with
  `white-space: pre-wrap`, a 70ch max measure, long words broken (`overflow-wrap: anywhere`).
  Messages over 12 lines are clipped with a "Show the whole message" button.
- **History:** heading "History". The lead events as a simple list, newest first, one plain
  sentence each with its time:

| Event | Sentence |
|---|---|
| `lead.created` | Arrived from {source} |
| `lead.placed` | Placed in {team} |
| `lead.claimed` | Claimed by {name} |
| `lead.released` | Released to the pool by {name} |
| `lead.returned` | Returned to the pool by {name} |
| `lead.reassigned` | Reassigned from {name} to {name} |
| `lead.moved_team` | Moved from {team} to {team} |
| `lead.activity` | New message from the buyer |
| anything else | The event type with dots and underscores turned into spaces |

  Names come from team members or the session user; an id that cannot be resolved reads "a teammate".
- 404: "This lead isn't available. It may have moved to a team you're not in." with a link back.
- Desktop layout: two columns, main 1fr (header, contact, facts, messages, history) and the
  280px action column. Phone: one column, action bar sticky at the bottom.

### 6.5 Unassigned (`/unassigned`, monitor and admin)

- Heading "Unassigned" and count "4 need a team". Rows (5.2) whose action is "Place in a
  team…": on desktop an inline select ("Team") and "Place" button sit in the action column, no
  extra click; on phones the button opens the inline panel.
- Placing: "Placing…", then the row collapses out and a toast "Placed in North." 409 or 404:
  the row dims with "Already placed by someone else."
- Empty: "Nothing is unassigned." / "Leads with no team land here. Right now every lead has one."

### 6.6 Waiting longest (`/waiting`, monitor and admin): the escalations view

- Heading "Waiting longest". One line under it, muted: "Unclaimed leads across every team,
  late ones first."
- Data: `GET /leads?view=unassigned` plus `GET /leads?view=pool&team_id=` for every team, merged
  and sorted by arrival (oldest first). Show only Late and Very late leads by default, with a
  toggle "Show all unclaimed" (a checkbox).
- Rows as 5.2 with a team column (Unassigned reads "Unassigned"); no Claim button (monitors do
  not claim); the name links to the detail, where Place is offered for Unassigned leads.
- A summary line above the list, plain text, no tiles: "3 very late, 5 late, across 4 teams."
- Empty: "No one has waited too long." / "Leads show here once they wait 15 minutes."

### 6.7 Not-a-lead tray (`/tray`, `/tray/:messageId`, monitor and admin)

- Heading "Not-a-lead tray" and one muted line: "Messages the rules read as vendor, support or
  spam. Move any real lead back to the leads."
- Filter: text tabs "All", "Vendor", "Support", "Spam" with counts, client-side.
- Rows: category badge, sender name (or email), received time, source, and the reason (muted,
  one line, truncated with the full text in the detail). Name links to the message.
- **Message page:** facts (from, phone, email, source, received, category, reason), then the
  full text in the same block as 6.4. Primary button "This is a lead: move it to the leads".
  On success: "Moved to the leads. It's waiting in Unassigned." with "Open the lead" (`lead_id`).
  409: "Already moved to the leads." with the same link if a `lead_id` is known.
- Desktop may show list and message side by side (list 400px, message the rest) when the
  viewport is 1100px or wider; below that they are separate pages.
- Empty: "The tray is empty." / "Nothing has been filed as vendor, support or spam."

### 6.8 Inbox (`/inbox`)

- In-app copies of the alert emails. Heading "Inbox" and "2 unread".
- Rows: subject (600 weight when unread, with a small accent dot and visually hidden "Unread"),
  time. Opening one shows the plain-text body (`text`) in a pre-wrap block and calls
  `POST /inbox/{id}/read`. Links in the text to lead pages may be made clickable only when they
  point to this app's own origin; everything else stays text.
- **Never render the `html` field.** It carries buyer text; showing it would need a sanitizer
  PR2 does not have.
- Empty: "No alerts yet." / "Alerts about new and late leads also land here."

### 6.9 Users (`/admin/users`, admin)

- Heading "Users" and a primary button "Add user".
- Desktop: a table with columns Name, Email, Roles, Teams (team name and "salesperson" or
  "team lead"), Status ("Active" or "Deactivated"), and an "Edit" link. Deactivated rows in
  `--text-muted`. Phone: a list, one user per row (name, email, role summary), tapping opens Edit.
- **Add user** (inline panel at the top, or its own page on phones): Name, Email, Password
  (optional; hint "Leave blank to set it later"), Org roles (checkboxes "Monitor", "Admin"),
  Teams (one row per team: a select "Not in this team / Salesperson / Team lead"). Button "Add user".
- **Edit user:** same fields minus email and password, plus "Set a new password" (inline panel,
  one field, "Set password"), and at the bottom a destructive "Deactivate user". Its panel says:
  "Deactivating Ana Ruiz returns their 4 open leads to their pools." (count from the leads the
  admin can list; if unknown, "returns their open leads to their pools") with "Deactivate" and
  "Cancel". A deactivated user shows "Reactivate" instead.
- Errors (422) show the API's message under the form in `--danger`, starting with a capital.
- Never show or echo a password after it is set; success toast "Password set."

### 6.10 Alerts on this device (`/alerts`)

One short page explaining and switching push for this browser.

- Heading "Alerts on this device". A status line with icon and one of these states:

| State | How it is known | Text | Control |
|---|---|---|---|
| Not set up on the server | `GET /push/key` gives `public_key: null` | "Push alerts aren't set up on this server. You'll still get alerts by email and in the Inbox." | none |
| Not supported | no `serviceWorker` or `PushManager` | "This browser can't show push alerts." | none |
| iPhone, not on Home Screen | iOS Safari and not `display-mode: standalone` | "On iPhone, add Firstcall to your Home Screen first: tap Share, then Add to Home Screen. Then open it from there." | none |
| Blocked | `Notification.permission === "denied"` | "Alerts are blocked for this site. Allow notifications in your browser's site settings, then come back." | none |
| Off | no subscription | "Push alerts are off on this device." | primary "Turn on alerts" |
| Turning on | in progress | "Asking your browser…" | disabled |
| On | subscription exists and was saved | "Push alerts are on. You'll get one alert per new lead in your teams." | secondary "Turn off" |

- Ask for permission only from the button click, never on page load.
- Under the status, muted: "Email alerts are always on. They group alerts that come within a
  minute into one email."
- Signed-in users without a subscription see a one-time, dismissible line at the top of Pool:
  "Get an alert the moment a lead arrives. Turn on alerts." Dismissal is remembered per browser.

## 7. Copy

Voice: short, plain, present tense, sentence case, no exclamation marks, no "Oops", no
"Successfully". Buttons are verbs. Times use the viewer's locale with a 24-hour or 12-hour
clock per the locale default.

| Where | Copy |
|---|---|
| Claim, working, won | Claim / Claiming… / You own this lead now. Open lead |
| Lost claim | Already claimed by a teammate |
| Lead moved on | This lead has moved on. |
| Pool empty | No one is waiting in {team}. New leads appear here the moment they arrive. |
| My leads empty | You don't own any leads yet. Claim one from your team's pool. |
| Unassigned empty | Nothing is unassigned. |
| Waiting empty | No one has waited too long. |
| Tray empty | The tray is empty. |
| Inbox empty | No alerts yet. |
| Load failed | Couldn't load {the pool / your leads / …}. Try again |
| Refresh stale | Not updated since {time}. Retrying. |
| Action failed | Couldn't {claim / place / release}. Check your connection and try again. |
| Signed out | You were signed out. Sign in to carry on. |
| Lead 404 | This lead isn't available. It may have moved to a team you're not in. |
| No name / no detail | No name given / No details yet |
| Release, return | Release to pool / Return to {team} pool |
| Rescue | This is a lead: move it to the leads |

## 8. Accessibility checklist (the builder checks each)

- Every page has one `<h1>`; lists are `<ul>` with a labelled heading; the admin table is a real `<table>` with `<th scope>`.
- All actions reachable and usable with the keyboard alone, in visual order; Escape closes panels and menus.
- Focus visible everywhere (3.3); focus is moved deliberately after claim, place, panel open and close.
- One polite live region per page for results ("Already claimed by a teammate", "Placed in North").
- Urgency is never colour alone (word plus icon plus `aria-label`).
- Text contrast per the token table; do not introduce new pairs without measuring them.
- Zoom to 200% and a 320px wide viewport with no horizontal scrolling; long names and emails wrap.
- `lang="en"`, page `<title>` per route ("North pool · Firstcall").
- Respect `prefers-reduced-motion`.

## 9. What the builder must not do

- **Never insert API text as HTML.** Names, items, message text, reasons, subjects and event
  payloads go in with `textContent` (or an equivalent that escapes). No `innerHTML` with data,
  ever. The F29 check renders a `<script>` buyer name and expects literal text.
- Never render the inbox `html` field.
- Never show an action the API's `actions` list did not include, and never call one it did not include.
- Never show a buyer's phone or email in list rows, toasts, page titles or push text.
- No card grids, dashboard tiles, big-number headers, charts or sparklines. This PR has no dashboard.
- No modals, no confirmation for claiming (speed matters; losing a claim is harmless).
- No colour outside the tokens; no gradients, glass, glow, or coloured side borders on rows.
- No emoji or Unicode symbols as icons; Lucide only.
- No web fonts from a CDN; no new runtime dependencies beyond the icon SVGs and the font files
  (the stack stays Vite plus TypeScript, no framework, unless the lead decides otherwise).
- Do not ask for notification permission on load.
- No marketing tone, mascot, or brand of any real company, and no names from the original
  homebuilder tool. Demo data uses the made-up company from `config/demo.yaml`.
- Do not change the API to suit the pages; note any gap for the lead instead (see Assumptions).

## 10. Assumptions the owner may want to change

1. **Urgency uses wall-clock minutes since arrival** (5 / 15 / 60). The API gives no business-minute
   wait for pool leads and no per-team thresholds, so a lead that arrived at night shows
   "Very late" in the morning even though its business clock just started. Fix: the API could add
   `wait_business_minutes` and the team's thresholds to each pool lead; the page would switch to them.
2. **There is no escalations route.** "Waiting longest" is built from the pool and Unassigned lists,
   so it shows who is late, not which escalation alerts actually fired. A `GET /escalations` would
   make it exact.
3. The 5-minute "Waiting" step is a design choice, not from the plan; the 15 and 60 steps match the plan's escalation defaults.
4. Lists refresh by polling every 15 seconds (no live push of data to the page exists).
5. No theme toggle; the pages follow the device's light or dark setting.
6. Font: Atkinson Hyperlegible Next. Icons: Lucide. Both are open-licensed and self-hosted.
7. No PRODUCT.md or DESIGN.md was written for the project; this brief is the only design source for PR2.
