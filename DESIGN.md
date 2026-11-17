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
  age-late: "#d2491f"
  age-verylate: "#a8191b"
  on-verylate: "#fbfcfc"
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
---

# Design System: Firstcall

## Overview

**Creative North Star: "On the Rail"**

Every lead is a printed ticket clipped to a steel rail at a kitchen pass. The page is brushed steel; anything a machine printed or a person wrote sits on bright thermal paper; ink is near-black. A ticket's top stripe ages from green to heat-lamp orange to late red while it waits, and a person claims it by pressing Heard. The world is an operating object, not a landing page: there is no headline beside a dashboard screenshot and no row of feature cards.

The system is plain and hard-edged. Three faces split the work by who is speaking: a condensed industrial sans for anything stamped or posted (headlines, labels, timers, keys), a narrow monospace for anything the printer produced, and a humanist text face for prose a person reads at length. Colour carries almost no decoration; the age colours exist only to say how long a lead has waited.

Scope today: the public front page (`web/index.html`, `web/src/front/`, `web/src/styles/rail-tokens.css`). The signed-in app (`web/app.html`, `web/src/styles/app.css`, `web/src/styles/tokens.css`) is not yet in this world and is restyled into it in a later phase. Its current look is not part of this system and is not a reference for new work.

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
- **Thermal Paper** (`paper`): every printed or written object: tickets, note slips, the tally slip, the house card, key fills.

### Neutral
- **Brushed Steel** (`steel`): the page ground.
- **Pale Steel** (`steel-light`): the suggestion block that waits for Confirm, so a pending action reads as set apart from what was already written.
- **Rail Steel** (`steel-dark`): the rail bar itself.
- **Rail Edge** (`steel-edge`): the rail's top lip and heavier bottom lip.
- **Soft Ink** (`ink-soft`): secondary prose on steel (the lede, the demo note, the footer).
- **Faded Print** (`paper-ink-soft`): secondary text on paper: field labels, team names, the raw email, margin notes, the tally's leader dots.
- **Paper Rule** (`paper-rule`): dashed rules between printed fields and under slip headers.

### Tertiary: the age stripes
- **Fresh Green** (`age-new`): a lead under 5 minutes old.
- **Heat-Lamp Orange** (`age-waiting`): Waiting, from 5 minutes.
- **Late Red** (`age-late`): Late, from 15 minutes.
- **Very Late Red** (`age-verylate`): Very late, from 60 minutes; also the fill behind the Very late word, with `on-verylate` text.

The thresholds are the app's own defaults, read through its urgency function, not front-page constants.

**The State-Only Rule.** The four age colours mean how long a lead has waited and nothing else. No link, heading, button, border or illustration uses them.

**The Stripe-Not-Text Rule.** Fresh Green, Heat-Lamp Orange and Late Red are stripes, never text or text grounds (Heat-Lamp Orange is 2.6:1 on paper). Every stripe has its label word beside it in ink, so colour is never the only signal.

**The Hottest-Colour Rule.** Only the lead that has waited longest gets its age word reversed out of Very Late Red. A claimed ticket's stripe turns to ink.

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

**The Slashed Zero Rule.** Figures inside prose stay in the text face, slashed zero and all. Atkinson's zero is slashed for legibility and has no plain alternate; switching faces mid-sentence reads worse.

## Layout

A single column of full-bleed sections on steel, with a fluid side gutter (`spacing.gutter`) shared by the top rule, every section and the footer. Sections are separated by `spacing.section`; the rail follows the intro with no gap, and the notes section sits 20px under the rail's caption line.

The rail is the spine: a 22px steel bar that breaks out of the gutter to run edge to edge, with tickets hanging from it. Tickets overlap the rail's lower lip by 6px, so the bar visibly grips the paper. At desktop the first ticket is about 2.3 times as wide as the other three; all sit in one row, top-aligned, separated by `spacing.rail-gap`.

Two-part sections (notes, proof) use an asymmetric two-column grid separated by `spacing.column-gap`: prose narrow on one side, paper objects on the other. Inside paper, content is inset by `spacing.ticket-inset`.

**Responsive.** Below 1100px the first ticket takes a full row above the other three, and the notes section stacks. Below 760px everything is one column: tickets stack under the rail so all four ages stay in view while scrolling, the received and printed halves of the first ticket stack with an ink rule between them, the house card's two columns stack, and the tally drops its leader dots and lets labels wrap.

## Elevation & Depth

Depth is physical and flat. Steel is the ground and never casts or takes a shadow. Paper sits on it with one shadow, `--paper-edge`, the thickness of a sheet. Steel structure (the rail) gets depth from its edge colour and heavier bottom lip, not from a shadow.

### Shadow Vocabulary
- **Paper edge** (`box-shadow: 0 1px 1px rgb(27 29 32 / 0.18), 0 3px 6px -2px rgb(27 29 32 / 0.22)`): tickets, note slips and the tally slip. Nothing else.

**The Paper-Only Shadow Rule.** Only loose paper casts a shadow, and only the paper edge. Posted cards (the house card) are framed in ink instead.

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
- **Stripe:** a 10px top band in the age colour (ink once claimed).
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
- **Don't** take the signed-in app's current styles as a reference until it is restyled into this world.
