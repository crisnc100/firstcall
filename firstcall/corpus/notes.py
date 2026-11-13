"""Note scripts: made-up salesperson notes with gold labels, for the note eval.

`firstcall corpus notes --seed N` writes evals/gold/notes/seed-N/{scripts,labels}.jsonl for
seeds 7 (tuning) and 11 (held out). Each seed has 150 scripts: 60 that record a completed
contact, 75 hard non-actions (15 each of future tense, negation, a plan, a hypothetical and a
colleague's contact) and 15 with neither.

Every sentence comes from a phrasing. Seen phrasings appear in both seeds; unseen phrasings
appear only in seed 11, so the held-out run includes wording the prompt was never tuned on.
Notes draw from their own random stream, so the message gold is unchanged by them.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from firstcall.core.note_terms import ACTION_KINDS, NON_ACTION_KINDS
from firstcall.corpus.generate import GOLD_DIR
from firstcall.corpus.world import World, load_world

NOTE_SEEDS = (7, 11)
UNSEEN_NOTE_SEEDS = (11,)
NOTES_DIR = GOLD_DIR / "notes"
SCRIPTS = "scripts.jsonl"
LABELS = "labels.jsonl"
TOTAL = 150
ACTION_SCRIPTS = 60
TWO_ACTION_SCRIPTS = 15
PER_NON_ACTION = 15
NEITHER = 15
# Each BANT field is present in this many of the 150 scripts: empty in 42% of them.
PRESENT = 87
# In seed 11 each sentence takes an unseen phrasing with this chance.
UNSEEN_SHARE = 0.2
FIRST_NOTE = date(2026, 3, 2)
NOTE_DAYS = 210

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip
NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
    9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
    15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
    20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy",
    80: "eighty", 90: "ninety",
}  # fmt: skip


@dataclass(frozen=True)
class Phrasing:
    id: str
    text: str
    seen: bool = True


def _p(pid: str, text: str) -> Phrasing:
    return Phrasing(pid, text, True)


def _u(pid: str, text: str) -> Phrasing:
    return Phrasing(pid, text, False)


# Slots: {name} the buyer's first name, {Name} the same at a sentence start, {they} and
# {their} the buyer's pronouns, {item} a listing, {colleague} a teammate's first name.
ACTIONS: dict[str, tuple[Phrasing, ...]] = {
    "call": (
        _p("call-1", "Called {name} this morning."),
        _p("call-2", "Spoke with {name} on the phone for a while."),
        _p("call-3", "{Name} called me back this afternoon."),
        _u("call-u1", "Got {name} on the phone after lunch."),
        _u("call-u2", "Had a quick phone chat with {name} today."),
    ),
    "text": (
        _p("text-1", "Texted {name} the listing link."),
        _p("text-2", "{Name} texted back to say {they} liked the photos."),
        _u("text-u1", "Sent {name} a text with the price sheet."),
        _u("text-u2", "Got a text reply from {name} around noon."),
    ),
    "email": (
        _p("email-1", "Emailed {name} the floor plans."),
        _p("email-2", "{Name} replied to my email last night."),
        _u("email-u1", "Fired off an email to {name} with the HOA documents."),
        _u("email-u2", "Sent the brochure over to {name} by email."),
    ),
    "meeting": (
        _p("meeting-1", "Met {name} at {item} for a tour."),
        _p("meeting-2", "Showed {name} around {item} today."),
        _u("meeting-u1", "Walked {name} through the model home at {item}."),
        _u("meeting-u2", "Sat down with {name} at the office to go over options."),
    ),
}
NON_ACTIONS: dict[str, tuple[Phrasing, ...]] = {
    "future": (
        _p("future-1", "Will call {name} tomorrow."),
        _p("future-2", "Going to email {name} the comps on Friday."),
        _u("future-u1", "Gonna text {name} over the weekend."),
        _u("future-u2", "I'll swing by {item} with {name} next week."),
    ),
    "negation": (
        _p("negation-1", "Didn't get to call {name} today."),
        _p("negation-2", "Haven't emailed {name} the floor plans yet."),
        _u("negation-u1", "No texts with {name} so far."),
        _u("negation-u2", "The meeting with {name} got pushed, so we never sat down."),
    ),
    "plan": (
        _p("plan-1", "Plan is to set up a tour at {item}."),
        _p("plan-2", "Need to text {name} the open house times."),
        _u("plan-u1", "Next step: phone {name} about financing."),
        _u("plan-u2", "On my list: send {name} the HOA documents by email."),
    ),
    "hypothetical": (
        _p("hypothetical-1", "If {name} doesn't reply by Monday I'll call."),
        _p("hypothetical-2", "Might email {name} the listing if it comes back on the market."),
        _u("hypothetical-u1", "Should {name} want a second look, we'd meet at {item}."),
        _u("hypothetical-u2", "Could text {name} if the price drops."),
    ),
    "colleague": (
        _p("colleague-1", "{colleague} called {name} last week, before the lead came to me."),
        _p("colleague-2", "{colleague} met {name} at the open house."),
        _u("colleague-u1", "{colleague} already emailed {name} the brochure."),
        _u("colleague-u2", "{colleague} handled the first call with {name}."),
    ),
}

# Budget wording. {amount} is the figure in the chosen number style.
BUDGET_AROUND = (
    _p("budget-around-1", "Budget is around {amount}."),
    _p("budget-around-2", "Looking to spend about {amount}."),
    _u("budget-around-u1", "Price point they keep coming back to is {amount}."),
)
BUDGET_UP_TO = (
    _p("budget-upto-1", "Can go up to {amount}."),
    _u("budget-upto-u1", "Hard ceiling of {amount}."),
)
BUDGET_AT_LEAST = (
    _p("budget-least-1", "Wants to spend at least {amount}."),
    _u("budget-least-u1", "Nothing under {amount} interests {them}."),
)
BUDGET_BETWEEN = (
    _p("budget-between-1", "Budget is between {low} and {high}."),
    _u("budget-between-u1", "Somewhere in the {low} to {high} range."),
)
# A qualifier after the figure, with the key term it carries.
BUDGET_TERMS = (
    ("{They} {are} pre-approved.", "pre-approved"),
    ("Paying cash.", "cash"),
    ("{They} {need} to sell {their} current place first.", "sell"),
)
SEEN_STYLES = ("k", "comma", "thousand", "words", "million")
UNSEEN_STYLES = ("upper_k", "grand", "short_m")

AUTHORITY: dict[str, tuple[tuple[Phrasing, tuple[str, ...]], ...]] = {
    "decision_maker": (
        (_p("authority-dm-1", "{They} {make} the call on this."), ()),
        (_p("authority-dm-2", "{Name} is the decision maker."), ()),
        (_u("authority-dm-u1", "Final say is {theirs}."), ()),
    ),
    "shares_decision": (
        (_p("authority-share-1", "{Their} {partner} has to sign off too."), ("{partner}",)),
        (_p("authority-share-2", "Deciding together with {their} {partner}."), ("{partner}",)),
        (_u("authority-share-u1", "Needs {their} business partner on board first."),
         ("business partner",)),
    ),
    "not_decision_maker": (
        (_p("authority-not-1", "Buying for {their} parents, who make the final call."),
         ("parents",)),
        (_u("authority-not-u1", "{Their} brother is the one paying, so it's his call."),
         ("brother",)),
    ),
}  # fmt: skip
PARTNERS = ("husband", "wife", "partner")

# Needs, each with its key term.
FEATURES = (
    ("a garage", "garage"),
    ("a home office", "home office"),
    ("a big yard", "yard"),
    ("a single-story layout", "single-story"),
    ("good schools nearby", "schools"),
    ("a walkable street", "walkable"),
    ("a pool", "pool"),
    ("a first-floor primary suite", "first-floor"),
    ("room for a dog", "dog"),
    ("a low HOA fee", "HOA"),
)
NEED = (
    _p("need-1", "Wants {bedrooms} bedrooms and {feature}."),
    _p("need-2", "Must have {feature}."),
    _p("need-3", "Looking for {bedrooms} bedrooms, ideally with {feature}."),
    _u("need-u1", "Non-negotiables: {bedrooms} bedrooms plus {feature}."),
    _u("need-u2", "Top of {their} wish list is {feature}."),
)

# Timeline: (phrasing, key term, kind). "by" names a month, "within" counts months ahead,
# "none" names no month.
TIMELINE = (
    (_p("timeline-by-1", "Wants to move by {month}."), "move", "by"),
    (_p("timeline-lease-1", "Lease is up at the end of {month}."), "lease", "by"),
    (_p("timeline-within-1", "Hoping to be in within {count} months."), "hoping", "within"),
    (_p("timeline-reloc-1", "Relocating for work in {month}."), "relocating", "by"),
    (_p("timeline-none-1", "No rush, just starting to look."), "no rush", "none"),
    (_u("timeline-sells-u1", "{Their} current place sells in {month}, so that's the target."),
     "sells", "by"),
    (_u("timeline-movein-u1", "Move-in goal is {month}."), "move-in", "by"),
    (_u("timeline-none-u1", "Timing is loose, sometime next year."), "next year", "none"),
)  # fmt: skip

EXTRAS = (
    "Has two dogs.",
    "Prefers a corner lot.",
    "Works from home most days.",
    "Asked about the commute downtown.",
    "Mentioned the open house went well.",
)


def number_words(value: int) -> str:
    """Whole numbers below a million, in words: 375000 is "three hundred seventy-five
    thousand"."""

    def under_thousand(n: int) -> str:
        parts: list[str] = []
        if n >= 100:
            parts.append(f"{NUMBER_WORDS[n // 100]} hundred")
            n %= 100
        if n >= 20:
            tens, ones = n - n % 10, n % 10
            parts.append(NUMBER_WORDS[tens] + (f"-{NUMBER_WORDS[ones]}" if ones else ""))
        elif n:
            parts.append(NUMBER_WORDS[n])
        return " ".join(parts)

    if value >= 1_000_000:
        raise ValueError("number words stop below a million")
    thousands, rest = divmod(value, 1000)
    words = []
    if thousands:
        words.append(f"{under_thousand(thousands)} thousand")
    if rest:
        words.append(under_thousand(rest))
    return " ".join(words)


def amount_text(value: int, style: str) -> str:
    if value >= 1_000_000:
        millions = f"{value / 1_000_000:g}"
        return {"short_m": f"${millions}M", "comma": f"${value:,}"}.get(
            style, f"${millions} million"
        )
    thousands = value // 1000
    return {
        "k": f"${thousands}k",
        "upper_k": f"{thousands}K",
        "comma": f"${value:,}",
        "thousand": f"{thousands} thousand",
        "words": number_words(value),
        "grand": f"{thousands} grand",
    }.get(style, f"${thousands}k")


def _month_after(day: date, months: int) -> tuple[int, int]:
    index = day.month - 1 + months
    return day.year + index // 12, index % 12 + 1


@dataclass
class Script:
    id: str
    note_date: date
    sentences: list[str] = field(default_factory=list[str])
    phrasings: list[str] = field(default_factory=list[str])
    seen: bool = True


class NoteGenerator:
    def __init__(self, seed: int, world: World) -> None:
        from faker import Faker

        self.seed = seed
        self.rng = random.Random(f"notes-{seed}")
        self.fake = Faker("en_US")
        self.fake.seed_instance(f"notes-{seed}")
        self.items = sorted(world.item_team)
        self.unseen_share = UNSEEN_SHARE if seed in UNSEEN_NOTE_SEEDS else 0.0

    def _first_name(self, female: bool) -> str:
        while True:
            name = self.fake.first_name_female() if female else self.fake.first_name_male()
            if name.isascii() and name.isalpha():
                return name

    def _pick(self, options: tuple[Phrasing, ...]) -> Phrasing:
        seen = [p for p in options if p.seen]
        unseen = [p for p in options if not p.seen]
        if unseen and self.rng.random() < self.unseen_share:
            return self.rng.choice(unseen)
        return self.rng.choice(seen)

    def _pick_with(self, options: tuple[tuple[Phrasing, Any, Any], ...]) -> tuple[Any, ...]:
        chosen = self._pick(tuple(o[0] for o in options))
        return next(o for o in options if o[0] is chosen)

    def _style(self) -> str:
        if self.rng.random() < self.unseen_share:
            return self.rng.choice(UNSEEN_STYLES)
        return self.rng.choice(SEEN_STYLES)

    def _amount(self) -> int:
        if self.rng.random() < 0.15:
            return self.rng.choice((1_000_000, 1_100_000, 1_200_000, 1_250_000, 1_500_000))
        return self.rng.randrange(250, 990, 5) * 1000

    def plan(self) -> list[dict[str, Any]]:
        """What each script will hold, before any wording is chosen."""
        kinds = ["action"] * ACTION_SCRIPTS
        for kind in NON_ACTION_KINDS:
            kinds += [kind] * PER_NON_ACTION
        kinds += ["neither"] * NEITHER
        assert len(kinds) == TOTAL
        self.rng.shuffle(kinds)
        presence: dict[str, list[bool]] = {}
        for name in ("budget", "authority", "need", "timeline"):
            flags = [True] * PRESENT + [False] * (TOTAL - PRESENT)
            self.rng.shuffle(flags)
            presence[name] = flags
        two_actions = set(
            self.rng.sample([i for i, k in enumerate(kinds) if k == "action"], TWO_ACTION_SCRIPTS)
        )
        return [
            {
                "kind": kind,
                "action_count": 2 if index in two_actions else (1 if kind == "action" else 0),
                **{name: presence[name][index] for name in presence},
            }
            for index, kind in enumerate(kinds)
        ]

    def render(self, index: int, spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        female = self.rng.random() < 0.5
        name = self._first_name(female)
        colleague = self._first_name(self.rng.random() < 0.5)
        while colleague == name:
            colleague = self._first_name(self.rng.random() < 0.5)
        pronouns = (
            {"they": "she", "them": "her", "their": "her", "theirs": "hers", "are": "is",
             "make": "makes", "need": "needs"}
            if female
            else {"they": "he", "them": "him", "their": "his", "theirs": "his", "are": "is",
                  "make": "makes", "need": "needs"}
        )  # fmt: skip
        item = self.rng.choice(self.items)
        note_date = FIRST_NOTE + timedelta(days=self.rng.randrange(NOTE_DAYS))
        script = Script(f"note-{self.seed}-{index:03d}", note_date)
        terms: dict[str, list[str]] = {"budget": [], "authority": [], "need": [], "timeline": []}
        slots = {
            "name": name,
            "Name": name,
            "item": item,
            "colleague": colleague,
            **pronouns,
            **{k.capitalize(): v.capitalize() for k, v in pronouns.items()},
        }

        def say(phrasing: Phrasing, **extra: str) -> None:
            script.sentences.append(phrasing.text.format(**slots, **extra))
            script.phrasings.append(phrasing.id)
            script.seen = script.seen and phrasing.seen

        contact: list[str] = []
        actions: list[str] = []
        if spec["kind"] == "action":
            for kind in self.rng.sample(ACTION_KINDS, spec["action_count"]):
                actions.append(kind)
                before = len(script.sentences)
                say(self._pick(ACTIONS[kind]))
                contact.append(script.sentences.pop(before))
            if self.rng.random() < 0.5:
                before = len(script.sentences)
                say(self._pick(NON_ACTIONS[self.rng.choice(NON_ACTION_KINDS)]))
                contact.append(script.sentences.pop(before))
        elif spec["kind"] in NON_ACTIONS:
            for _ in range(self.rng.choice((1, 1, 2))):
                before = len(script.sentences)
                say(self._pick(NON_ACTIONS[spec["kind"]]))
                sentence = script.sentences.pop(before)
                if sentence not in contact:
                    contact.append(sentence)

        budget = None
        if spec["budget"]:
            form = self.rng.choice(("around", "up_to", "at_least", "between"))
            style = self._style()
            script.phrasings.append(f"style-{style}")
            script.seen = script.seen and style in SEEN_STYLES
            if form == "between":
                low = self._amount()
                high = low + self.rng.choice((25_000, 50_000, 75_000, 100_000))
                say(self._pick(BUDGET_BETWEEN), low=amount_text(low, style),
                    high=amount_text(high, style))  # fmt: skip
                budget = {"min": low, "max": high}
            else:
                value = self._amount()
                options = {"around": BUDGET_AROUND, "up_to": BUDGET_UP_TO,
                           "at_least": BUDGET_AT_LEAST}[form]  # fmt: skip
                say(self._pick(options), amount=amount_text(value, style))
                budget = {
                    "around": {"min": value, "max": value},
                    "up_to": {"min": None, "max": value},
                    "at_least": {"min": value, "max": None},
                }[form]
            if self.rng.random() < 0.6:
                text, term = self.rng.choice(BUDGET_TERMS)
                script.sentences.append(text.format(**slots))
                terms["budget"].append(term)

        authority = "unknown"
        if spec["authority"]:
            authority = self.rng.choice(tuple(AUTHORITY))
            partner = self.rng.choice(PARTNERS)
            phrasing, found = self._pick_with(tuple((p, t, None) for p, t in AUTHORITY[authority]))[
                :2
            ]
            say(phrasing, partner=partner)
            terms["authority"] = [t.format(partner=partner) for t in found]

        need = None
        if spec["need"]:
            feature, term = self.rng.choice(FEATURES)
            bedrooms = NUMBER_WORDS[self.rng.choice((2, 3, 4, 5))]
            say(self._pick(NEED), bedrooms=bedrooms, feature=feature)
            need = True
            terms["need"].append(term)

        timeline = None
        if spec["timeline"]:
            phrasing, term, kind = self._pick_with(TIMELINE)
            month = None
            if kind == "by":
                year, number = _month_after(note_date, self.rng.randrange(1, 11))
                say(phrasing, month=MONTH_NAMES[number - 1])
                month = f"{year:04d}-{number:02d}"
            elif kind == "within":
                count = self.rng.randrange(2, 10)
                say(phrasing, count=NUMBER_WORDS[count])
                year, number = _month_after(note_date, count)
                month = f"{year:04d}-{number:02d}"
            else:
                say(phrasing)
            timeline = {"month": month}
            terms["timeline"].append(term)

        body = script.sentences
        self.rng.shuffle(body)
        if (not body and not contact) or self.rng.random() < 0.3:
            body.append(self.rng.choice(EXTRAS))
        text = " ".join(contact + body)

        kind = spec["kind"]
        scripted = {"id": script.id, "note_date": note_date.isoformat(), "text": text}
        label = {
            "id": script.id,
            "note_date": note_date.isoformat(),
            # Some notes never name the buyer.
            "buyer": name if name in text else None,
            "kind": kind,
            "actions": sorted(actions),
            "budget": budget,
            "authority": authority,
            "need": need,
            "timeline": timeline,
            "terms": terms,
            "phrasings": script.phrasings,
            "seen": script.seen,
        }
        return scripted, label


def _lines(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)


def generate_notes(seed: int, world: World | None = None) -> tuple[str, str]:
    """Scripts and labels for a seed, as written to disk."""
    generator = NoteGenerator(seed, world or load_world())
    scripts: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for index, spec in enumerate(generator.plan(), start=1):
        script, label = generator.render(index, spec)
        scripts.append(script)
        labels.append(label)
    return _lines(scripts), _lines(labels)


def write_notes(seed: int, root: Path = NOTES_DIR) -> dict[str, str]:
    scripts, labels = generate_notes(seed)
    folder = root / f"seed-{seed}"
    folder.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name, text in ((SCRIPTS, scripts), (LABELS, labels)):
        (folder / name).write_text(text)
        hashes[name] = hashlib.sha256(text.encode()).hexdigest()
    return hashes


def read_jsonl(seed: int, name: str, root: Path = NOTES_DIR) -> list[dict[str, Any]]:
    text = (root / f"seed-{seed}" / name).read_text()
    return [json.loads(line) for line in text.splitlines()]


NOTES_MANIFEST = NOTES_DIR / "MANIFEST.json"


def notes_file_hashes(seed: int, root: Path = NOTES_DIR) -> dict[str, str]:
    folder = root / f"seed-{seed}"
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.glob("*.jsonl"))
    }


def read_notes_manifest(path: Path = NOTES_MANIFEST) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())["seeds"]


def record_notes_seed(seed: int, hashes: dict[str, str], path: Path = NOTES_MANIFEST) -> None:
    seeds = read_notes_manifest(path)
    seeds[str(seed)] = hashes
    body = {"seeds": dict(sorted(seeds.items(), key=lambda kv: int(kv[0])))}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
