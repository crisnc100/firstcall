"""The rules read: buyer contact, item, agent, team hint, spam signals and source time, pulled
from a raw message with fixed patterns and the source's field mapping. No model is involved.

Three kinds of contact are kept from the buyer: an agent's (listed per org, or found by
phrasing), a portal relay sender's (listed per source) and a call-tracking number (listed per
source). The read leaves them out of free-text scans, the sender and Reply-To fallbacks and
mapped form fields; an agent message also leaves out everything from the signature on. A
value under a label in a relay body, or in its "Name (contacts)" sentence, is taken as
written. Matching then drops any of these contacts that still arrive, so none becomes a
buyer's identity. Lines that address an assistant or tell the reader to ignore its
instructions are skipped when scanning free text, so text written to steer a reader cannot
supply a contact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import EmailPolicy
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from typing import Any, cast

from firstcall.core.identity import (
    email_domain,
    find_emails,
    find_phones,
    normalize_email,
    normalize_phone,
)


class UnparseableError(Exception):
    """The message has nothing a rule can read."""


class OverBudgetError(UnparseableError):
    """The message is larger than the read budget. It is never cut short to fit, because a
    cut could drop the very line that names the buyer or the agent."""


# The read budget. Every pattern below runs in time linear in its input, and this budget
# bounds that input, so one message cannot hold a worker for long. Real leads are far
# smaller: the largest message in the synthetic corpus is under 2,000 characters.
MAX_TEXT_CHARS = 200_000
MAX_LINES = 4_000
MAX_LINE_CHARS = 4_000
MAX_HEADER_BYTES = 64_000
MAX_PARTS = 100
# Headers capped at MAX_LINE_CHARS each: the ones the rules read (From, Reply-To, Subject,
# Date, and Message-ID at intake), the ones the email library parses to find the body
# (Content-Type, Content-Disposition, Content-Transfer-Encoding), and To, Sender and
# MIME-Version, which are capped though not read. Every other header counts only toward the
# total above; the library can still parse Content-ID inside multipart/related.
PARSED_HEADERS = frozenset(
    {
        "from", "to", "reply-to", "sender", "subject", "date", "message-id", "content-type",
        "content-disposition", "content-transfer-encoding", "mime-version",
    }
)  # fmt: skip
MAX_DEPTH = 10


@dataclass(frozen=True)
class AgentRef:
    name: str | None
    email: str | None
    phone: str | None


@dataclass(frozen=True)
class SourceRules:
    kind: str
    field_mapping: Mapping[str, str] = field(default_factory=dict[str, str])
    relay_senders: frozenset[str] = frozenset()
    tracking_numbers: frozenset[str] = frozenset()
    spam_senders: frozenset[str] = frozenset()
    honeypot_field: str | None = None


@dataclass(frozen=True)
class Reference:
    """What the org has configured: its items of interest and its listed agents."""

    items: tuple[str, ...]
    agents: tuple[AgentRef, ...] = ()


@dataclass(frozen=True)
class Reading:
    category: str
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    item: str | None = None
    team_hint: str | None = None
    agent: AgentRef | None = None
    spam_signals: tuple[str, ...] = ()
    source_time: datetime | None = None
    text: str = ""
    # Names of fields a rule filled only from a free-text scan, not from a label or a mapped
    # field ("phone", "email", "item"), plus "name" when no name was found at all. Only the
    # relay and form paths set it; agent and direct emails leave it empty.
    unsure: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "item": self.item,
            "team_hint": self.team_hint,
            "agent": None
            if self.agent is None
            else {"name": self.agent.name, "email": self.agent.email, "phone": self.agent.phone},
            "spam_signals": list(self.spam_signals),
            "source_time": self.source_time.isoformat() if self.source_time else None,
            "unsure": list(self.unsure),
        }


# Possessive quantifiers never give back what they took, so a run of capitalised words
# cannot make the match retry every split.
NAME = r"[A-Z][A-Za-z'\-]++(?:[ \t]++[A-Z][A-Za-z'\-]++)++"
INSTRUCTION = re.compile(
    r"ignore (?:all |your |the |any )?(?:previous |prior |earlier )?(?:instructions|rules)"
    r"|note (?:to|for) the ai|\bassistant\b|^\s*system\s*:|\bthe ai\b",
    re.IGNORECASE,
)
LABEL_NAME = re.compile(r"[A-Za-z][A-Za-z \-]{0,40}")
LABEL_SEPARATORS = (":", "..", "|")
LABELS = {
    "name": {"name", "contact name", "buyer"},
    "phone": {"phone", "tel", "contact phone"},
    "email": {"email", "e-mail", "contact email"},
    "item": {"listing", "property", "home"},
}
SENTENCE_LEAD = re.compile(rf"^(?P<name>{NAME}) \((?P<contacts>[^\n]*)\) is interested in", re.M)
FROM_LINE = re.compile(r"^From:[ \t]*(?P<value>[^\n]+)$", re.M)
REPLY_TO_LINE = re.compile(r"^Reply-To:[ \t]*(?P<value>[^\n]+)$", re.M | re.IGNORECASE)

AGENT_PHRASES = (
    re.compile(rf"on behalf of (?:my|our) client,?[ \t]+(?P<name>{NAME})"),
    re.compile(rf"\b[Mm]y client,?[ \t]+(?P<name>{NAME})"),
    re.compile(rf"\brepresent (?:a|the) buyer,?[ \t]+(?P<name>{NAME})"),
)
CALLER_INTRO = re.compile(rf"\b(?:this is|my name is)[ \t]+(?P<name>{NAME})")
# A closing word or a "--" signature separator starts the sender's signature.
CLOSING = re.compile(
    r"^(?:(?:best regards|kind regards|regards|thanks|thank you|sincerely|cheers|best),?|--)$"
)
FORWARD_MARKER = re.compile(
    r"^-{2,}[ \t]*(?:forwarded message|original message)[ \t]*-{2,}[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
FORWARD_TIMES = (
    re.compile(
        r"^Date:[ \t]*(?P<when>\w{3}, \w{3} \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M)[ \t]*$", re.M
    ),
    re.compile(
        r"^Sent:[ \t]*(?P<when>\w{1,9}, \w{1,9} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M)[ \t]*$", re.M
    ),
)
FORWARD_FORMATS = ("%a, %b %d, %Y at %I:%M %p", "%A, %B %d, %Y %I:%M %p")


def read(raw: bytes, rules: SourceRules, reference: Reference) -> Reading:
    """The rules read. Raises UnparseableError, or OverBudgetError for a message over the
    read budget, which the pipeline records as a failed message with its reason."""
    try:
        if rules.kind == "email":
            return _read_email(raw, rules, reference)
        return _read_json(raw, rules, reference)
    except RecursionError as error:
        # The email library parses some headers only when they are fetched, and a header
        # or JSON value nested deeply enough overflows the stack there.
        raise OverBudgetError("over the read budget: nested too deeply") from error


def _lines_of(text: str) -> str:
    """Line endings as LF, so CRLF mail splits on the same blank lines as LF mail."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def check_header_budget(raw: bytes) -> None:
    """Refuse a message whose header block, or any one header in PARSED_HEADERS, is over the
    budget, before any header is parsed. Header parsing time grows faster than a header's
    length."""
    ends = [at for at in (raw.find(b"\n\n"), raw.find(b"\r\n\r\n")) if at >= 0]
    head = raw[: min(ends)] if ends else raw
    if len(head) > MAX_HEADER_BYTES:
        raise OverBudgetError(
            f"over the read budget: {len(head)} bytes of headers (limit {MAX_HEADER_BYTES})"
        )
    # Only headers that get read or parsed have a length cap, as in the worker's parse;
    # a long unread header such as References counts only toward the total.
    longest = 0
    current = 0
    name = b""
    for line in head.split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            # A line that starts with a space or tab continues the header above it.
            current += len(line)
        else:
            current = len(line)
            name = line.split(b":", 1)[0].strip().lower()
        if name.decode("latin-1") in PARSED_HEADERS:
            longest = max(longest, current)
    if longest > MAX_LINE_CHARS:
        raise OverBudgetError(
            f"over the read budget: a header of {longest} bytes (limit {MAX_LINE_CHARS})"
        )


TAG_NAME = re.compile(r"</?([A-Za-z][A-Za-z0-9]{0,9})")
# Tags that start a new line on the page. Table cells are left out: a label cell and its
# value cell share a row, and the row's own tag already ends the line.
BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "tr", "li", "ul", "ol", "table", "tbody", "thead", "tfoot",
        "h1", "h2", "h3", "h4", "h5", "h6", "hr", "section", "article", "header", "footer",
        "blockquote", "pre", "dl", "dt", "dd", "address",
    }
)  # fmt: skip


def _tag_end(html: str, opens: int) -> int:
    """Where the tag starting at `opens` ends, or -1 if it never does. A ">" inside a quoted
    attribute value, or inside a comment, does not end it. One pass over the tag."""
    if html.startswith("<!--", opens):
        closes = html.find("-->", opens + 4)
        return closes + 2 if closes >= 0 else -1
    quote = ""
    last = ""
    for at in range(opens + 1, len(html)):
        char = html[at]
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'" and last == "=":
            quote = char
        elif char == ">":
            return at
        if not char.isspace():
            last = char
    return -1


def _strip_tags(html: str) -> str:
    """HTML with each block tag replaced by a line break and every other tag or comment by a
    space, so labelled fields in paragraphs, rows or br lines stay on their own lines. Each
    tag is scanned once and the next search starts after it, so the cost is linear in the
    text. Text after a "<" that never closes is kept, as a browser would show it."""
    parts: list[str] = []
    at = 0
    while True:
        opens = html.find("<", at)
        if opens < 0:
            parts.append(html[at:])
            break
        closes = _tag_end(html, opens)
        if closes < 0:
            parts.append(html[at:])
            break
        parts.append(html[at:opens])
        tag = TAG_NAME.match(html, opens)
        parts.append("\n" if tag and tag.group(1).lower() in BLOCK_TAGS else " ")
        at = closes + 1
    return "".join(parts)


def _check_budget(text: str) -> None:
    if len(text) > MAX_TEXT_CHARS:
        raise OverBudgetError(
            f"over the read budget: {len(text)} characters (limit {MAX_TEXT_CHARS})"
        )
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        raise OverBudgetError(f"over the read budget: {len(lines)} lines (limit {MAX_LINES})")
    longest = max(len(line) for line in lines)
    if longest > MAX_LINE_CHARS:
        raise OverBudgetError(
            f"over the read budget: a line of {longest} characters (limit {MAX_LINE_CHARS})"
        )


def _plain_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not INSTRUCTION.search(line))


def _find_item(text: str, items: tuple[str, ...]) -> str | None:
    folded = text.casefold()
    best: tuple[int, str] | None = None
    for item in items:
        at = folded.find(item.casefold())
        if at >= 0 and (best is None or at < best[0]):
            best = (at, item)
    return best[1] if best else None


def _label_line(line: str) -> tuple[str, str] | None:
    """A "Label: value" line, also with dotted leaders or table pipes, read with string
    methods so the cost stays linear in the line."""
    text = line.strip().lstrip("|").strip()
    cuts = [(text.find(sep), sep) for sep in LABEL_SEPARATORS if text.find(sep) > 0]
    if not cuts:
        return None
    at, sep = min(cuts)
    label = text[:at].strip()
    if not LABEL_NAME.fullmatch(label):
        return None
    rest = text[at + len(sep) :]
    value = (rest.lstrip(".") if sep == ".." else rest).strip().rstrip(" \t|").strip()
    return label.casefold(), value


def _labels(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.split("\n"):
        parsed = _label_line(line)
        if parsed is None:
            continue
        label, value = parsed
        for field_name, names in LABELS.items():
            if label in names and value and field_name not in found:
                found[field_name] = value
    return found


def _is_spam_sender(email: str | None, rules: SourceRules) -> bool:
    if not email:
        return False
    listed = {s.lower() for s in rules.spam_senders}
    return email in listed or email_domain(email) in listed


def _agent_for(email: str | None, phones: list[str], reference: Reference) -> AgentRef | None:
    for agent in reference.agents:
        if email and agent.email and email == agent.email.lower():
            return agent
    for agent in reference.agents:
        if agent.phone and agent.phone in phones:
            return agent
    return None


def _excluded(rules: SourceRules, reference: Reference) -> tuple[set[str], set[str]]:
    phones = set(rules.tracking_numbers) | {a.phone for a in reference.agents if a.phone}
    emails = {s.lower() for s in rules.relay_senders} | {
        a.email.lower() for a in reference.agents if a.email
    }
    return phones, emails


def _first(values: list[str], excluded: set[str]) -> str | None:
    return next((v for v in values if v not in excluded), None)


def _body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = str(cast(Any, part).get_content())
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    content = _lines_of(content)
    _check_budget(content)
    if part.get_content_subtype() == "html":
        # Entities are decoded after the tags are gone, so an escaped "<" stays text. A
        # no-break space reads as a plain space, so "Ana&nbsp;Ruiz" is "Ana Ruiz".
        content = unescape(_strip_tags(content)).replace("\u00a0", " ")
    return content


def _address(value: object) -> tuple[str | None, str | None]:
    pairs = getaddresses([str(value)]) if value else []
    if not pairs:
        return None, None
    display, address = pairs[0]
    return (display.strip() or None), normalize_email(address)


def _date(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _forward(
    body: str, zone: tzinfo
) -> tuple[str, str | None, str | None, datetime | None, str | None]:
    """The forwarded part, its sender's display name and address, its sent time, and its
    Reply-To address."""
    markers = list(FORWARD_MARKER.finditer(body))
    if not markers:
        return body, None, None, None, None
    # The marker pattern stops at its own line end; blank lines between the marker and the
    # forwarded headers belong to neither.
    inner = body[markers[-1].end() :].lstrip()
    head = inner.split("\n\n", 1)[0]
    sender = FROM_LINE.search(head)
    display, address = _address(sender.group("value")) if sender else (None, None)
    reply = REPLY_TO_LINE.search(head)
    reply_to = _address(reply.group("value"))[1] if reply else None
    sent: datetime | None = None
    for pattern in FORWARD_TIMES:
        match = pattern.search(head)
        if match:
            for fmt in FORWARD_FORMATS:
                try:
                    sent = datetime.strptime(match.group("when"), fmt).replace(tzinfo=zone)
                    break
                except ValueError:
                    continue
    rest = inner.split("\n\n", 1)
    return (rest[1] if len(rest) > 1 else ""), display, address, sent, reply_to


class _Budget:
    """Header and part counts for one message, filled while the parser reads it."""

    def __init__(self) -> None:
        self.header_bytes = 0
        self.parts = 0
        self.parsed: dict[tuple[str, str], Any] = {}


class _BudgetPolicy(EmailPolicy):
    """The default email policy, with every header of every part, nested ones included,
    checked against the read budget as the parser meets it. A header that gets parsed is
    refused before that parse, whose time grows faster than the header's length."""

    budget: _Budget | None = None

    def header_source_parse(self, sourcelines: list[str]) -> tuple[str, str]:
        budget = self.budget
        assert budget is not None
        size = sum(len(line) for line in sourcelines)
        name = sourcelines[0].split(":", 1)[0].strip().lower()
        if name in PARSED_HEADERS and size > MAX_LINE_CHARS:
            raise OverBudgetError(
                f"over the read budget: a header of {size} characters (limit {MAX_LINE_CHARS})"
            )
        budget.header_bytes += size
        if budget.header_bytes > MAX_HEADER_BYTES:
            raise OverBudgetError(
                f"over the read budget: {budget.header_bytes} characters of headers "
                f"(limit {MAX_HEADER_BYTES})"
            )
        return super().header_source_parse(sourcelines)

    def header_fetch_parse(self, name: str, value: str) -> Any:
        """A parsed header, kept for the rest of this message. The feed parser fetches the
        parent's Content-Type once per child part, so without this a long header would be
        parsed again for every part, up to the part limit. Parsed headers do not change, and
        the cache holds at most the distinct headers inside the header budget."""
        budget = self.budget
        if budget is None or hasattr(value, "name"):
            return super().header_fetch_parse(name, value)
        key = (name.lower(), value)
        if key not in budget.parsed:
            budget.parsed[key] = super().header_fetch_parse(name, value)
        return budget.parsed[key]


def _depth(message: EmailMessage) -> int:
    """How deeply parts nest, walked without recursion."""
    deepest = 0
    stack: list[tuple[Any, int]] = [(message, 1)]
    while stack:
        part, level = stack.pop()
        deepest = max(deepest, level)
        payload = part.get_payload()
        if isinstance(payload, list):
            stack.extend((child, level + 1) for child in cast(list[Any], payload))
    return deepest


def _parse(raw: bytes) -> EmailMessage:
    """The message under the default policy, inside the read budget. Each message gets a fresh
    budget. Nesting deep enough to exhaust Python's stack is refused the same way."""
    budget = _Budget()
    try:

        def new_part(policy: Any = None) -> EmailMessage:
            # The parser calls this for the message and for every part, with or without
            # headers, so the count is of real parts.
            budget.parts += 1
            if budget.parts > MAX_PARTS:
                raise OverBudgetError(f"over the read budget: more than {MAX_PARTS} parts")
            return EmailMessage(policy=policy)

        options: dict[str, Any] = {"budget": budget, "message_factory": new_part}
        parse_policy: EmailPolicy = _BudgetPolicy(**options)
        message = BytesParser(policy=parse_policy).parsebytes(raw)
        depth = _depth(cast(EmailMessage, message))
    except RecursionError as error:
        raise OverBudgetError("over the read budget: parts nested too deeply") from error
    if depth > MAX_DEPTH:
        raise OverBudgetError(
            f"over the read budget: parts nested {depth} deep (limit {MAX_DEPTH})"
        )
    return cast(EmailMessage, message)


def _read_email(raw: bytes, rules: SourceRules, reference: Reference) -> Reading:
    message = _parse(raw)
    display, sender = _address(message.get("From"))
    body = _body_text(message)
    if not sender and not body.strip():
        raise UnparseableError("no sender and no text")
    subject = str(message.get("Subject") or "")
    header_time = _date(message.get("Date"))
    zone = header_time.tzinfo if header_time and header_time.tzinfo else UTC
    content, inner_display, inner_sender, forwarded_at, inner_reply = _forward(body, zone)
    reply_to = _address(message.get("Reply-To"))[1]
    if inner_sender:
        # The forwarded message is the lead; the forwarder's own headers say nothing of it.
        display, sender, reply_to = inner_display, inner_sender, inner_reply
    source_time = forwarded_at or header_time
    excluded_phones, excluded_emails = _excluded(rules, reference)
    relays = {s.lower() for s in rules.relay_senders}
    plain = _plain_lines(content)
    all_phones = find_phones(plain)
    signals = ("spam_sender",) if _is_spam_sender(sender, rules) else ()
    category = "spam" if signals else "lead"
    item_text = subject + "\n" + plain
    labels = _labels(plain)
    unsure: list[str] = []

    if sender in relays:
        name = labels.get("name")
        email = normalize_email(labels.get("email"))
        phone = normalize_phone(labels.get("phone"))
        sentence = SENTENCE_LEAD.search(plain)
        from_line = FROM_LINE.search(plain)
        if not name and sentence:
            name = sentence.group("name")
            contacts = sentence.group("contacts")
            email = email or normalize_email(contacts)
            phone = phone or _first(find_phones(contacts), excluded_phones)
        if from_line and (not name or not email):
            line_name, line_email = _address(from_line.group("value"))
            name = name or line_name
            email = email or line_email
        if not email and reply_to and reply_to not in excluded_emails:
            email = reply_to
        if not email:
            email = _first(find_emails(plain), excluded_emails)
            if email:
                unsure.append("email")
        if not phone and "phone" not in labels:
            phone = _first(all_phones, excluded_phones)
            if phone:
                unsure.append("phone")
        if not name:
            unsure.append("name")
        item = _find_item(labels.get("item", ""), reference.items) or _find_item(
            item_text, reference.items
        )
        return Reading(
            category=category,
            name=name,
            phone=phone,
            email=email,
            item=item,
            spam_signals=signals,
            source_time=source_time,
            text=body,
            unsure=tuple(unsure),
        )

    agent = _agent_for(sender, all_phones, reference)
    phrase = next((m for p in AGENT_PHRASES if (m := p.search(plain))), None)
    if agent or phrase:
        lines = plain.splitlines()
        cut = next(
            (i for i, line in enumerate(lines) if CLOSING.match(line.strip().casefold())), None
        )
        before = "\n".join(lines[:cut]) if cut is not None else plain
        signature = "\n".join(lines[cut:]) if cut is not None else ""
        if agent is None:
            agent = AgentRef(display, sender, _first(find_phones(signature), set()))
        own_phones = excluded_phones | {p for p in (agent.phone,) if p}
        own_emails = excluded_emails | {e for e in (sender, agent.email) if e}
        phone = _first(find_phones(before), own_phones)
        email = _first(find_emails(before), own_emails)
        name = phrase.group("name") if phrase else None
        item = _find_item(item_text, reference.items)
        return Reading(
            category=category,
            name=name,
            phone=phone,
            email=email,
            item=item,
            agent=agent,
            spam_signals=signals,
            source_time=source_time,
            text=body,
        )

    phone = _first(all_phones, excluded_phones)
    email = sender if sender not in excluded_emails else None
    item = _find_item(item_text, reference.items)
    return Reading(
        category=category,
        name=display,
        phone=phone,
        email=email,
        item=item,
        spam_signals=signals,
        source_time=source_time,
        text=body,
    )


def _path(payload: Any, dotted: str) -> Any:
    value = payload
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = cast(dict[str, Any], value).get(part)
    return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in cast(dict[str, Any], value).values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in cast(list[Any], value) for s in _strings(v)]
    return []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_json(raw: bytes, rules: SourceRules, reference: Reference) -> Reading:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise UnparseableError("not JSON") from error
    if not isinstance(payload, dict):
        raise UnparseableError("JSON is not an object")
    payload = cast(dict[str, Any], payload)
    mapping = rules.field_mapping

    def mapped(name: str) -> str | None:
        return _text(_path(payload, mapping[name])) if name in mapping else None

    excluded_phones, excluded_emails = _excluded(rules, reference)
    all_text = _lines_of("\n".join(_strings(payload)))
    _check_budget(all_text)
    leaves = _plain_lines(all_text)
    unsure: list[str] = []
    name = mapped("name")
    if not name:
        parts = [p for p in (mapped("first_name"), mapped("last_name")) if p]
        name = " ".join(parts) or None
    message = _lines_of(mapped("message") or "")
    if not name and rules.kind == "call":
        intro = CALLER_INTRO.search(_plain_lines(message))
        name = intro.group("name") if intro else None
    phone = normalize_phone(mapped("phone"))
    if phone in excluded_phones:
        phone = None
    if phone is None and mapped("phone") is None:
        phone = _first(find_phones(leaves), excluded_phones)
        if phone:
            unsure.append("phone")
    email = normalize_email(mapped("email"))
    if email in excluded_emails:
        email = None
    if email is None and mapped("email") is None:
        email = _first(find_emails(leaves), excluded_emails)
        if email:
            unsure.append("email")
    item = _find_item(mapped("item") or "", reference.items) or _find_item(
        _plain_lines(message), reference.items
    )
    if item is None and mapped("item") is None:
        item = _find_item(leaves, reference.items)
        if item:
            unsure.append("item")
    if not name:
        unsure.append("name")
    signals: list[str] = []
    if rules.honeypot_field and _text(payload.get(rules.honeypot_field)):
        signals.append("honeypot")
    if _is_spam_sender(email, rules):
        signals.append("spam_sender")
    category = "spam" if signals else "lead"
    return Reading(
        category=category,
        name=name,
        phone=phone,
        email=email,
        item=item,
        team_hint=mapped("team"),
        spam_signals=tuple(signals),
        text=message,
        unsure=tuple(unsure),
    )
