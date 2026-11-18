---
name: Firstcall
description: An open-source lead hub where every lead is a printed ticket on a steel rail.
colors:
  steel: "#c9ced2"
  steel-light: "#dfe3e6"
  steel-dark: "#5b6268"
  steel-edge: "#3a4045"
  ink: "#1b1d20"
  ink-soft: "#3b4046"
  paper: "#fbfcfc"
  paper-ink-soft: "#53585c"
  paper-rule: "#c4cacd"
  age-new: "#2b7a4b"
  age-waiting: "#e3861c"
  age-late: "#d12d1f"
  age-verylate: "#a8191b"
  on-verylate: "#fbfcfc"
  on-steel: "#1b1d20"
  on-steel-soft: "#3b4046"
typography:
  display:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "clamp(3.25rem, 6.2vw, 5.5rem)"
    fontWeight: 800
    lineHeight: 0.95
    letterSpacing: "-0.005em"
  headline:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "clamp(2.25rem, 4vw, 3.5rem)"
    fontWeight: 800
    lineHeight: 0.95
  title:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 800
    lineHeight: 0.95
    letterSpacing: "0.14em"
  wordmark:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 800
    letterSpacing: "0.01em"
  figure:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "1.625rem"
    fontWeight: 800
    lineHeight: 1
    fontFeature: "tnum"
  label:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "1rem"
    fontWeight: 800
    letterSpacing: "0.14em"
  label-small:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 700
    lineHeight: 1.6
    letterSpacing: "0.12em"
  body:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.5
  lede:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "clamp(1.0625rem, 1.3vw, 1.1875rem)"
    fontWeight: 400
    lineHeight: 1.45
  print:
    fontFamily: "Martian Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.5
    fontVariation: "'wdth' 87.5"
  app-title:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "2rem"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "0.01em"
  app-heading:
    fontFamily: "Big Shoulders, Arial Narrow, sans-serif-condensed, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "0.1em"
  app-body:
    fontFamily: "Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  print-raw:
    fontFamily: "Martian Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.6875rem"
    fontWeight: 500
    lineHeight: 1.3
    fontVariation: "'wdth' 87.5"
rounded:
  none: "0px"
spacing:
  gutter: "clamp(16px, 3.9vw, 56px)"
  rail-gap: "clamp(14px, 1.6vw, 24px)"
  column-gap: "clamp(24px, 4vw, 64px)"
  section: "clamp(64px, 8vw, 112px)"
  ticket-inset: "16px"
  app-1: "4px"
  app-2: "8px"
  app-3: "12px"
  app-4: "16px"
  app-5: "24px"
  app-6: "32px"
  app-7: "48px"
components:
  button-heard:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "9px 12px 8px"
  button-heard-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  stamp-claimed:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "9px 12px 8px"
  ticket:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.print}"
    rounded: "{rounded.none}"
  age-word-verylate:
    backgroundColor: "{colors.age-verylate}"
    textColor: "{colors.on-verylate}"
    typography: "{typography.label}"
    padding: "1px 6px"
  house-card:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
  house-card-header:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    padding: "14px 24px 12px"
  nav-link:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
  app-key:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "3px 14px 0"
    height: "44px"
  app-key-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  app-key-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "3px 14px 0"
    height: "44px"
  # The fill mixed 80% with its label colour (color-mix in srgb): #484a4c by day and on
  # paper; on steel at night, on-steel with ink, #babcbf.
  app-key-primary-hover:
    backgroundColor: "#484a4c"
    textColor: "{colors.paper}"
  lead-ticket:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.app-body}"
    rounded: "{rounded.none}"
    padding: "12px 16px 12px 24px"
  stamp:
    textColor: "{colors.ink}"
    typography: "{typography.label-small}"
    rounded: "{rounded.none}"
    padding: "2px 6px 0"
  field:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.app-body}"
    rounded: "{rounded.none}"
    padding: "0 12px"
    height: "44px"
  posted-card:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "16px"
  toast:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.app-body}"
    rounded: "{rounded.none}"
    padding: "12px 16px"
  team-stamp-current:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
---

# Design System: Firstcall

## Overview

**Creative North Star: "On the Rail"**

Every lead is a printed ticket clipped to a steel rail at a kitchen pass. The page is brushed steel; anything a machine printed or a person wrote sits on bright thermal paper; ink is near-black. A ticket's stripe (along its top on the front page, down its left edge in the app) ages from green to heat-lamp orange to late red while it waits, and a person claims it by pressing Heard. The world is an operating object, not a landing page: there is no headline beside a dashboard screenshot and no row of feature cards.

The system is plain and hard-edged. Three faces split the work by who is speaking: a condensed industrial sans for anything stamped or posted (headlines, labels, timers, keys), a narrow monospace for anything the printer produced, and a humanist text face for prose a person reads at length. Colour carries almost no decoration; the age colours exist only to say how long a lead has waited.

Scope: the public front page (`web/index.html`, `web/src/front/`) and the signed-in app (`web/app.html`, `web/src/styles/app.css`). Both read every colour, face and spacing step from `web/src/styles/rail-tokens.css`. The front page persuades; the app is worked in all day, so it keeps the world's palette, faces, square edges and stripes, and spends them on scanning speed rather than spectacle.

**Key Characteristics:**
- Steel ground, paper objects, ink type; nothing else competes.
- Age colours are state, never decoration.
- Square corners everywhere; the only softness is the paper's own edge shadow.
- Three faces, each tied to a speaker: the pass, the printer, the person.
- State changes are instant; the one motion is the printer feeding the first ticket.

## Colors

A cool, near-neutral steel and paper palette with four saturated age colours held in reserve for state.

### Primary
- **Ticket Ink** (`ink`): all headlines, body text on steel, the top and footer rules, borders of keys and the house card, the claimed stripe, focus rings, text selection.
- **Thermal Paper** (`paper`): every printed or written object: tickets, note slips, the tally slip, the house card, key fills. In the app also every lead, list, table, message, form card and menu.

### Neutral
- **Brushed Steel** (`steel`): the page ground.
- **Pale Steel** (`steel-light`): the suggestion block that waits for Confirm, so a pending action reads as set apart from what was already written.
- **Rail Steel** (`steel-dark`): the rail bar itself.
- **Rail Edge** (`steel-edge`): the rail's top lip and heavier bottom lip.
- **Soft Ink** (`ink-soft`): secondary prose on steel (the lede, the demo note, the footer).
- **On Steel** (`on-steel`, `on-steel-soft`): text set straight on the steel ground in the app. By day they equal Ticket Ink and Soft Ink; at night they turn light while ink on paper stays dark, which is why the app never reads `ink` for text on steel.
- **Faded Print** (`paper-ink-soft`): secondary text on paper: field labels, team names, the raw email, margin notes, the tally's leader dots.
- **Paper Rule** (`paper-rule`): dashed rules between printed fields and under slip headers.

### Tertiary: the age stripes
- **Fresh Green** (`age-new`): a lead under 5 minutes old.
- **Heat-Lamp Orange** (`age-waiting`): Waiting, from 5 minutes.
- **Late Red** (`age-late`): Late, from 15 minutes. A true red (hue 5), so beside Waiting's orange it never reads as a second orange; Very late stays apart as the darker crimson with its word reversed out.
- **Very Late Red** (`age-verylate`): Very late, from 60 minutes; also the fill behind the Very late word, with `on-verylate` text.

The thresholds are the app's own defaults, read through its urgency function, not front-page constants.

**The State-Only Rule.** The four age colours mean how long a lead has waited and nothing else. No link, heading, button, border or illustration uses them.

**The Stripe-Not-Text Rule.** Fresh Green, Heat-Lamp Orange and Late Red are stripes, never text or text grounds (Heat-Lamp Orange is 2.6:1 on paper). Every stripe has its label word beside it in ink, so colour is never the only signal.

**The Hottest-Colour Rule.** Only the lead that has waited longest gets its age word reversed out of Very Late Red. A claimed ticket's stripe turns to ink. In the app, where several leads can be very late at once, every Very late word is reversed: there it is a state, and the board sorts the longest wait first.

### Night shift (app only)
When the device asks for dark, the app moves to a night palette; the front page stays in daylight. The ground goes to dark steel (`#1e2225`), the rail turns lighter than the ground (`#444c52`, lips `#6a737a`) like steel catching the light, so it never reads as a black slab, and paper dims to `#b9bec0` so it still reads as paper under the lights. Ink on paper stays dark (`#14161a`), Faded Print darkens to `#393e42`, Pale Steel becomes `#a9afb2`, and text on steel turns light (`#e3e6e8`, soft `#a7aeb3`). The four age colours keep their hues.

Measured contrast, day / night: text on steel 10.7 / 12.8; muted text on steel 6.6 / 7.1; ink on paper 16.4 / 9.7; Faded Print on paper 7.0 / 5.8; ink on Pale Steel 13.1 / 8.2; Faded Print on Pale Steel 5.6 / 4.9; the Very late word 7.2 / 6.8. Stripes against paper: New 5.1 / 2.8, Waiting 2.7 / 1.5, Late 5.0 / 2.7, Very late 7.2 / 4.0. Late against Waiting 1.9, against Very late 1.5. The word beside every stripe carries the state, so the stripe is never the only signal.

### Errors
Errors stay in ink, set bold with the alert mark beside them. Red belongs to the age stripes, so an error never borrows it.

## Typography

**Display / Label Font:** Big Shoulders (with Arial Narrow, then a condensed sans)
**Body Font:** Atkinson Hyperlegible Next (with system-ui)
**Print Font:** Martian Mono (with ui-monospace, Menlo)

**Character:** Big Shoulders is the stencilled sign over the pass: condensed, heavy, uppercase. Martian Mono, narrowed with its width axis, is the thermal printer. Atkinson Hyperlegible Next is a person explaining, plain and easy to read.

### Hierarchy
- **Display** (800, uppercase): the one-line promise. One per page.
- **Headline** (800, uppercase): section titles.
- **Title** (800, uppercase, wide tracking): sub-heads such as "It does" and "It never".
- **Figure** (800, tabular): a ticket's running timer, so digits do not jitter as it counts.
- **Label** (800, uppercase, 0.10 to 0.16em tracking): nav links, the Heard and Confirm keys, the age word.
- **Label small** (700, uppercase, 0.12 to 0.14em tracking): field names and slip headers, on paper only (Buyer, Wants, Received, As typed).
- **Body** (400): explanatory prose, capped at about 40ch beside a figure and 62 to 70ch alone.
- **Lede** (400): the two sentences under the promise.
- **Print** (500, width 87.5%): everything the printer produced: ticket fields, note text, the tally. Field values on the large ticket's printed side narrow further to 75% width.
- **Print raw** (500): a message excerpt as it arrived.

**The Three Speakers Rule.** Pick the face by who is speaking: the pass (Big Shoulders), the printer (Martian Mono), a person (Atkinson). Prose never runs in Big Shoulders, and printed output never runs in Atkinson. Atkinson is the page's default face; Big Shoulders and Martian Mono are opted into per element, so new prose cannot come out condensed.

### In the app
The app uses a fixed rem scale. **App title** (2rem, 1.75rem on a phone) names the page; **App heading** (1.25rem, 0.1em tracking) names a section; both are uppercase Big Shoulders. Nav tabs, keys, stamps, counts and the age word are Label. Field labels on paper are Label small. Body and form text is **App body** (1rem Atkinson); a lead's name is Atkinson 700, because people read names, not the printer. The printer's face covers what the system stamped: team and arrival lines, times, history times, phone numbers and emails in a lead's fields, a nameless lead's number, the raw message and note text, and the measured "claimed in" and "first contact" lines. The running timer is Figure, as on the front ticket. A lead's name on its own page is Big Shoulders 800 in its own case, since it is data.

**The Slashed Zero Rule.** Figures inside prose stay in the text face, slashed zero and all. Atkinson's zero is slashed for legibility and has no plain alternate; switching faces mid-sentence reads worse.

## Layout

A single column of full-bleed sections on steel, with a fluid side gutter (`spacing.gutter`) shared by the top rule, every section and the footer. Sections are separated by `spacing.section`; the rail follows the intro with no gap, and the notes section sits 20px under the rail's caption line.

The rail is the spine: a 22px steel bar that breaks out of the gutter to run edge to edge, with tickets hanging from it. Tickets overlap the rail's lower lip by 6px, so the bar visibly grips the paper. At desktop the first ticket is about 2.3 times as wide as the other three; all sit in one row, top-aligned, separated by `spacing.rail-gap`.

Two-part sections (notes, proof) use an asymmetric two-column grid separated by `spacing.column-gap`: prose narrow on one side, paper objects on the other. Inside paper, content is inset by `spacing.ticket-inset`.

**Responsive.** Below 1100px the first ticket takes a full row above the other three, and the notes section stacks. Below 760px everything is one column: tickets stack under the rail so all four ages stay in view while scrolling, the received and printed halves of the first ticket stack with an ink rule between them, the house card's two columns stack, and the tally drops its leader dots and lets labels wrap.

### The app
A sticky top rule on steel carries the wordmark, the tabs this person may use and, at right, the Inbox count and the account key; a 2px rule runs under it. Pages sit in a 1120px column with a 24px gutter (16px on a phone). On a phone the tabs move to a bottom bar under a 2px rule, and the page title moves into the top rule.

A list of leads is the rail again, in the front page's proportions: a 22px steel bar with a 2px top lip and a 5px bottom lip, running the full width of the list and 8px past it on each side, with the first ticket hanging 6px under the bottom lip and the rest below it in one column with 8px of steel between them. A ticket row runs timer and age word, name and item, the printed team and arrival line, then the key, in a four-column grid; on a phone it stacks to timer and key, name, then the printed line. A lead's own page puts its printed facts, slips and messages in a column capped near 760px, with the posted card of keys beside it at 280px; on a phone the card pins above the bottom bar with only its first key and More actions showing.

## Elevation & Depth

Depth is physical and flat. Steel is the ground and never casts or takes a shadow. Paper sits on it with one shadow, `--paper-edge`, the thickness of a sheet. Steel structure (the rail) gets depth from its edge colour and heavier bottom lip, not from a shadow.

### Shadow Vocabulary
- **Paper edge** (`box-shadow: 0 1px 1px rgb(27 29 32 / 0.18), 0 3px 6px -2px rgb(27 29 32 / 0.22)`): tickets, note slips and the tally slip; in the app also lead rows, plain lists, tables, field lists, message slips, empty states, the account menu and the toast. At night it deepens to black at 0.5 and 0.6 so paper still lifts off dark steel. Nothing else.

**The Paper-Only Shadow Rule.** Only loose paper casts a shadow, and only the paper edge. Posted cards (the house card; in the app the sign-in card, a lead's card of keys, panels and notices) are framed in ink instead.

## Shapes

Every corner is square (`rounded.none`). Edges are drawn with ink rules at three weights: 1px for a hairline join, 2px for the top rule, footer rule and keys, 3px for the posted house card. Dashed rules in Paper Rule separate printed fields, the way a receipt prints them.

A ticket's bottom edge is a fine saw-tooth cut, where the printer's blade went through the paper. One solid ink rule joins related paper: the received email to the printed fields, the note as typed to the note as read.

**The One Rule Rule.** When two pieces of paper are the same thing seen twice, a single 1px ink rule connects them. No arrows, no icons.

## Components

### Heard key
Tactile and blunt: the button you slap at the pass.
- **Shape:** square, 2px ink border.
- **Default:** paper fill, ink label in the label style, tracked 0.16em. Reads "Heard" on a ticket ("Heard — Claim" on the first), "Confirm" or "Dismiss" under a suggestion.
- **Hover:** fill and text invert to ink and paper.
- **Active:** drops 1px.
- **Focus:** the global 3px ink outline, offset 3px.
- **Paired keys** (Confirm, Dismiss) split the width equally with a 10px gap.

### Claimed stamp
- Replaces the Heard key at once, with no fade: ink fill, paper text, centred, reading "Claimed by you · 0:42" with the time it waited. Focus moves to it.

### Ticket
The signature component.
- **Background:** paper, with the paper-edge shadow and the saw-cut bottom.
- **Stripe:** a 10px top band in the age colour (ink once claimed). In the app's lead rows the same stripe runs 8px down the left edge instead (see Lead ticket).
- **Head:** source and team stacked at left in small tracked caps, team in Faded Print; timer and age word stacked at right.
- **Body:** field list in print type, label column in small caps, values in mono, dashed rules between rows. A missing value prints "Not given".
- **The large ticket** splits into Received (the raw email excerpt) and Printed by the reader, joined by the one ink rule, with a margin note under them.

### Margin note
- Text face at 0.8125rem in Faded Print, set off by a 1px left rule. It is a person's aside explaining the paper it sits on.

### Note slip and tally slip
- Paper with the paper-edge shadow, 16px inset, print type. A small tracked-caps header sits over a dashed rule. The tally runs one figure per line, dotted out to a right-aligned bold figure.
- The suggestion block inside a slip sits on Pale Steel under a 2px ink rule: it is the only part that waits for a person.

### House card
- A posted rule card: paper, 3px ink frame, no shadow, max 1080px. Its title is reversed out of a full ink band; its two columns are split by a 3px ink rule.

### Navigation
- A top rule: the wordmark at left, three uppercase tracked links at right, a 2px ink rule under the bar. Links underline on hover. On narrow screens the bar wraps.

### Links
- Ink, underlined at 2px with a 0.18em offset; 3px on hover. No colour change.

## App components

### Keys
- Every app button is a key: square, 44px tall (48px for Claim on a phone), a 2px frame, the label in Label at 0.9375rem with 0.12em tracking.
- **Primary** (Claim, Save, Sign in, Place): filled with the surface's text colour, its label reversed out: ink with a paper label by day and on paper, the light on-steel colour with an ink label on steel at night. Hover mixes the fill a fifth toward the label. One primary per group.
- **Secondary**: paper fill, ink label; hover inverts to ink, like Heard. On steel at night the key is open (no fill, light frame and label), so the filled primary still outranks it.
- **Danger** (Delete lead): a 4px double ink frame, so it never passes for an ordinary key. No red.
- Pressed keys drop 1px. A disabled key fades to 55%. A busy key keeps its width and shows the spinner beside its working word.
- The password field's Show key sits inside the field, frameless.

### Lead ticket
- A paper row with the paper edge and an 8px age stripe down its left, gripped edge: New, Waiting, Late, Very late, or ink once someone owns it. The stripe follows the timer's own urgency class, so it changes exactly when the word does.
- Cristian approved the left-edge stripe for the app on 2026-11-18, over the front page's top band, because it scans down a long list.
- Timer in Figure over the age word in Label; only Very late is reversed out of its colour. The age word carries no icon at any width: the stripe and the word already say it, and an icon would push Very late onto a second line beside a long timer on a 390px phone. A name in Atkinson 700 links to the lead; a nameless lead shows its phone or email in print. The item line takes the stamps (Possible duplicate, Follow-up due).
- A claim lost to a teammate keeps the ticket on the rail, turns its stripe to ink and replaces the key with "Already claimed by a teammate" in Label.

### Stamps
- Small tracked caps in a 1px frame: a lead's state, a message's status, Handled, Confirmed. Stamps sit beside text, never over it.

### Fields and forms
- Paper fill, a 1px Faded Print frame and a 2px ink baseline, square, 44px tall. Labels sit above in Atkinson 600. Checkboxes take the text colour of the surface they sit on.
- Panels that open under a key are posted: paper, 2px ink frame. Inside a lead's card of keys they open on Pale Steel under a 2px ink rule instead, because they wait for a person.

### Printed fields
- A lead's facts, its buyer profile and a tray message's details are one paper slip: Label small names in Faded Print on the left, values on the right, dashed Paper Rule between rows. On a phone the name sits over its value.

### Note slip (app)
- The note as typed, in print, then one ink rule, then the tidied note in Atkinson. A suggested contact still waiting for Confirm or Dismiss is set on Pale Steel under a 2px ink rule; a settled one is a dashed line with its stamp.

### Lists and tables
- Plain lists (Inbox, the tray, escalations, Account) are one paper strip with dashed rules between rows. An Inbox row is one wide frameless button; the open row, time included, sits on Pale Steel edge to edge, and unread ones carry a square ink mark and bold subject. An open alert's links to its leads are secondary keys under its slip, in the slip's width.
- Tables are paper with Label small headers over a 2px ink rule and dashed rules between rows. A row's own header is set like its cells, only bolder.

### Navigation (app)
- Top tabs are Label in Soft Ink; the current one turns to ink with a 4px bar over the top rule. Team tabs are stamps that fill with ink when current; the Waiting / Owned switch under them is a tab row. The phone's bottom bar shows icon and short word, the current one in ink with a 4px bar on its top edge.
- The Inbox count is a square of the text colour with the ground colour reversed out.

### Toast
- Loose paper with the paper edge and a 2px ink rule on top, at the bottom right (full width above the bottom bar on a phone). One at a time; it never carries an error.

### Focus
- A 3px ring in the text colour of the surface it sits on, offset 2px: ink on paper, the steel text colour on steel, so it shows by day and at night.

## Do's and Don'ts

### Do:
- **Do** put anything printed or written on paper and everything else directly on steel.
- **Do** pair every age stripe with its word in ink: New, Waiting, Late, Very late.
- **Do** change state instantly: a claimed ticket swaps its key for the stamp with no transition.
- **Do** keep motion to the printer: the first ticket's printed fields feed in once on load (900ms, `cubic-bezier(0.16, 1, 0.3, 1)`, after 350ms), and only when the visitor allows motion.
- **Do** label every figure built on made-up data as made-up, on the same line.

### Don't:
- **Don't** use an age colour for anything that is not a lead's waiting time.
- **Don't** round a corner.
- **Don't** shadow anything that is not loose paper, and don't use any shadow but the paper edge.
- **Don't** set prose in Big Shoulders or printed output in Atkinson.
- **Don't** build the SaaS landing page: no headline beside a dashboard screenshot, no row of feature cards.
- **Don't** use red for an error or a destructive key in the app; weight, the alert mark and the double frame carry it.
- **Don't** read `ink` for text on steel in the app; read `on-steel`, or the night shift loses it.
