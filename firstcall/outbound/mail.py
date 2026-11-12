"""Email: one alert digest per user and team, rendered with buyer text escaped.

Two backends: SMTP (Mailpit locally) and the in-app inbox. Headers carry no buyer text but
a first name, with control characters removed, so a crafted name cannot add a header.
"""

from __future__ import annotations

import re
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from typing import Protocol

from jinja2 import Environment
from sqlalchemy.orm import Session

from firstcall.db import InboxEmail, User

CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
LABELS = {
    "new_lead": "New lead",
    "release": "Back in the pool",
    "return": "Back in the pool",
    "owner_left": "Back in the pool",
    "place": "Placed in your pool",
    "move_team": "Moved to your pool",
    "assigned": "Assigned to you",
    "activity": "New activity",
}

TEXT = """{{ heading }}
{% for line in lines %}
- {{ line.label }}: {{ line.name }}{% if line.item %} ({{ line.item }}){% endif %}
  {{ line.url }}
{% endfor %}
Open Firstcall to claim or follow up.
"""

HTML = """<!doctype html>
<html><body>
<p>{{ heading }}</p>
<ul>
{% for line in lines %}<li>{{ line.label }}: <a href="{{ line.url }}">{{ line.name }}</a>
{%- if line.item %} ({{ line.item }}){% endif %}</li>
{% endfor %}</ul>
<p>Open Firstcall to claim or follow up.</p>
</body></html>
"""

_text = Environment(autoescape=False, keep_trailing_newline=True).from_string(TEXT)  # noqa: S701
_html = Environment(autoescape=True).from_string(HTML)


def label(kind: str) -> str:
    if kind.startswith("escalation:"):
        return "Still unclaimed"
    return LABELS.get(kind, "Alert")


def header_safe(text: str | None, limit: int = 40) -> str:
    return CONTROL.sub("", text or "").strip()[:limit]


@dataclass(frozen=True)
class Line:
    kind: str
    name: str
    item: str | None
    url: str
    # The in-app path, which the inbox offers as the link; the URL above is for email.
    path: str = ""

    @property
    def label(self) -> str:
        return label(self.kind)


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str
    links: tuple[tuple[str, str], ...] = ()


def render(team: str, lines: list[Line]) -> Rendered:
    """The digest. The subject names a buyer only by first name, and only when it holds one."""
    if len(lines) == 1:
        first = header_safe(lines[0].name.split(" ")[0] if lines[0].name else "")
        subject = f"{lines[0].label} in {header_safe(team)}" + (f": {first}" if first else "")
    elif all(line.kind == "new_lead" for line in lines):
        subject = f"{len(lines)} new leads in {header_safe(team)}"
    else:
        subject = f"{len(lines)} alerts in {header_safe(team)}"
    heading = subject if len(lines) > 1 else f"{lines[0].label} in {team}"
    context = {"heading": heading, "lines": lines, "team": team}
    links = tuple((line.name, line.path) for line in lines if line.path)
    return Rendered(header_safe(subject, 200), _text.render(context), _html.render(context), links)


class Mailer(Protocol):
    def send(self, session: Session, user: User, mail: Rendered, now: datetime) -> None: ...


class SmtpMailer:
    def __init__(self, host: str, port: int, sender: str, timeout: float = 10.0) -> None:
        self.host, self.port, self.sender, self.timeout = host, port, sender, timeout

    def send(self, session: Session, user: User, mail: Rendered, now: datetime) -> None:
        message = EmailMessage(policy=SMTP)
        message["From"] = self.sender
        message["To"] = user.email
        message["Subject"] = mail.subject
        message.set_content(mail.text)
        message.add_alternative(mail.html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.send_message(message)


class InboxMailer:
    """Keeps the email in the app, for the beta, where no real email is sent."""

    def send(self, session: Session, user: User, mail: Rendered, now: datetime) -> None:
        session.add(
            InboxEmail(
                org_id=user.org_id,
                user_id=user.id,
                subject=mail.subject,
                text_body=mail.text,
                html_body=mail.html,
                links=[{"label": label, "path": path} for label, path in mail.links],
                created_at=now,
            )
        )
