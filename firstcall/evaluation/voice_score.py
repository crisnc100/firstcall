"""The voice eval's scorer, frozen with the voice gold before any transcription code (ADR-005).

A memo's transcript is read by the note reader and the answer is scored by the frozen note
scorer against the script the voice read, not against the transcript, so a misheard figure
counts as an error. Two things are added here, for voice only:
- **Word error rate.** Lower case, no punctuation, and each number read by the note scorer's
  own reader, so "$425k" and "four hundred twenty-five thousand dollars" are the same word.
  Punctuation inside a word is dropped ("H.O.A." is "hoa", "doesn't" is "doesnt"), and two
  spoken number forms the note scorer does not read are folded here, for WER only: a decimal
  said digit by digit ("one point one million") and a worded number before "grand" ("seven
  hundred five grand").
- **Names by sound.** No transcriber can hear how a name is spelled. A name in the answer that
  the script spells another way ("Steven" for "Stephen") counts as supported when the two are
  in the same group of evals/gold/voice/name-spellings.json. Numbers keep the strict rule. The
  strict count, spelling included, is reported on its own line beside the floor's.

A change to this file or the table after the voice gold is committed needs an entry in
evals/gold/CHANGELOG.md, and none is allowed once the seed-11 voice runs have started.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from firstcall.corpus.voice import VOICE_DIR
from firstcall.evaluation import note_score
from firstcall.evaluation.note_score import (
    BREAKS,
    JOINERS,
    SCALES,
    UNITS,
    _fold_words,
    _Number,
    _tokens,
    _words,
    _words_value,
    unsupported,
)

SPELLINGS = VOICE_DIR / "name-spellings.json"
UNSUPPORTED_MEASURE = "notes with an unsupported number or name"
# A script writes "$", which is no word, where a voice says "dollars".
UNSPOKEN = frozenset({"-", "dollar", "dollars"})
NAME_FINDING = re.compile(r"[a-z_.]+: name (?P<name>\w+)")
# Single letters joined by full stops: "H.O.A.", "a.m.". A full stop after the last letter
# stays, as it may end a sentence.
INITIALISM = re.compile(r"\b[a-z](?:\.[a-z])+\b", re.IGNORECASE)
INNER_APOSTROPHE = re.compile(r"(?<=[a-z])['\u2019](?=[a-z])", re.IGNORECASE)
DIGIT_WORDS = {"oh": 0, **{word: value for word, value in UNITS.items() if value < 10}}
GRAND = 1_000


def _whole_part(item: object) -> bool:
    return isinstance(item, str) and (item == "hundred" or item in UNITS)


def _spoken_decimals(items: list[_Number | str]) -> list[_Number | str]:
    """A worded number, "point", then digits said one by one, and an optional scale, as one
    number: "one point one million" is 1100000. "Price point" is left alone."""
    out: list[_Number | str] = []
    index = 0
    while index < len(items):
        start = len(out)
        while start and _whole_part(out[start - 1]):
            start -= 1
        after = index + 1
        while after < len(items) and items[after] in DIGIT_WORDS:
            after += 1
        if items[index] != "point" or start == len(out) or after == index + 1:
            out.append(items[index])
            index += 1
            continue
        whole = _words_value([str(word) for word in out[start:]])
        digits = "".join(str(DIGIT_WORDS[str(word)]) for word in items[index + 1 : after])
        scale = None
        if after < len(items) and (items[after] in SCALES or items[after] == "grand"):
            scale = SCALES.get(str(items[after]), GRAND)
            after += 1
        del out[start:]
        out.append(_Number(Decimal(f"{whole}.{digits}") * (scale or 1), scale))
        index = after
    return out


def _grand(items: list[_Number | str]) -> list[_Number | str]:
    """A worded number before "grand" is thousands, as "705 grand" is to the note scorer."""
    out: list[_Number | str] = []
    for item in items:
        last = out[-1] if out else None
        if item == "grand" and isinstance(last, _Number) and last.scale is None:
            out[-1] = _Number(last.value * GRAND, GRAND)
        else:
            out.append(item)
    return out


def words(text: str) -> list[str]:
    """The words WER compares: lower case, no punctuation, every number as its value. A bare
    number under a thousand joined to a scaled one takes its scale, as in the note scorer's
    reader, so both read "between 380 and 420 thousand" alike."""
    text = INITIALISM.sub(lambda match: match.group().replace(".", ""), text)
    text = INNER_APOSTROPHE.sub("", text)
    out: list[str] = []
    for clause in BREAKS.split(text):
        items = _grand(_fold_words(_spoken_decimals(_tokens(clause))))
        for index, item in enumerate(items):
            if not isinstance(item, _Number):
                if item not in UNSPOKEN:
                    out.append(item)
                continue
            value = item.value
            joined = items[index + 2] if index + 2 < len(items) else None
            if (
                item.scale is None
                and value < 1000
                and isinstance(joined, _Number)
                and items[index + 1] in JOINERS
                and joined.scale is not None
            ):
                value *= joined.scale
            out.append(format(value.normalize(), "f"))
    return out


def edits(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Substitutions, deletions and insertions that turn the reference into the hypothesis."""
    previous = list(range(len(hypothesis) + 1))
    for i, said in enumerate(reference, start=1):
        current = [i]
        for j, heard in enumerate(hypothesis, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (said != heard))
            )
        previous = current
    return previous[-1]


def wer(pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Word error rate over (script, transcript) pairs: all edits over all script words."""
    errors = total = 0
    for script, transcript in pairs:
        reference = words(script)
        errors += edits(reference, words(transcript))
        total += len(reference)
    return {"errors": errors, "words": total, "wer": round(errors / total, 4) if total else None}


@cache
def spelling_groups(path: Path = SPELLINGS) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(group) for group in json.loads(path.read_text())["groups"])


def same_sound(name: str) -> frozenset[str]:
    """The name and every spelling of it in the table that sounds the same."""
    for group in spelling_groups():
        if name in group:
            return group
    return frozenset({name})


def _heard(finding: str, script_words: set[str]) -> bool:
    match = NAME_FINDING.fullmatch(finding)
    if match is None:
        return False
    return any(name.casefold() in script_words for name in same_sound(match["name"]))


def unsupported_by_sound(script: str, answer: Mapping[str, Any] | None) -> list[str]:
    """The note scorer's unsupported findings, less names the script spells another way."""
    script_words = _words(script)
    return [f for f in unsupported(script, answer) if not _heard(f, script_words)]


def score_part(
    scripts: Mapping[str, str],
    labels: Sequence[Mapping[str, Any]],
    answers: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any]:
    """The note scorer's metrics, with unsupported names judged by sound and the strict
    count kept as `unsupported_strict`."""
    part = note_score.score_part(scripts, labels, answers)
    ids = [
        label["id"]
        for label in labels
        if unsupported_by_sound(scripts[label["id"]], answers.get(label["id"]))
    ]
    return {
        **part,
        "unsupported": {"count": len(ids), "ids": ids},
        "unsupported_strict": part["unsupported"],
    }


def floors(part: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The notes floors, the unsupported one counting names by sound."""
    return [
        {**row, "measure": f"{UNSUPPORTED_MEASURE}, names by sound"}
        if row["measure"] == UNSUPPORTED_MEASURE
        else row
        for row in note_score.floors(part)
    ]


def strict_line(part: Mapping[str, Any]) -> dict[str, Any]:
    """The unsupported count with names by spelling, reported beside the floor, with no floor
    of its own."""
    return {
        "measure": f"{UNSUPPORTED_MEASURE}, names by spelling",
        "floor": "reported",
        "value": part["unsupported_strict"]["count"],
        "met": None,
    }


def all_met(part: Mapping[str, Any]) -> bool:
    return all(row["met"] for row in floors(part))


def score(
    scripts: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    answers: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any]:
    """All scripts, then the seen and unseen phrasing apart, as the note scorer splits them.
    Floors apply to all scripts."""
    texts = {s["id"]: s["text"] for s in scripts}
    whole = score_part(texts, labels, answers)
    return {
        "all": {**whole, "floors": floors(whole), "strict": strict_line(whole)},
        "seen": score_part(texts, [lb for lb in labels if lb["seen"]], answers),
        "unseen": score_part(texts, [lb for lb in labels if not lb["seen"]], answers),
    }
