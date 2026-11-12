"""The model read: one structured-output call per message, through the gateway.

It never decides on its own whether a message is a lead. It returns the model's answer, or
the reason there is none, and the pipeline combines the answer with the rules read field by
field. A replay miss and a non-synthetic input in record mode are raised, not turned into a
rules-only result, so a broken eval or recording fails loudly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import Any, cast

import yaml

from firstcall import failpoints
from firstcall.core.model_read import (
    DEFAULT_METHODS,
    FIELDS,
    METHODS,
    ModelAnswer,
    answer_schema,
    parse_answer,
)
from firstcall.core.read import Reading
from firstcall.db import Message
from firstcall.model.gateway import Gateway, ModelRequest, ModelUnavailableError
from firstcall.model.prompts import READ_MESSAGE

FAILPOINT_DURING_MODEL_READ = "during_model_read"
FIELD_METHODS = Path(__file__).resolve().parents[2] / "config" / "field_methods.yaml"
UNREADABLE = "unreadable"
# The headers a person would look at to tell who wrote and who to answer.
EMAIL_HEADERS = ("From", "Reply-To", "To", "Subject", "Date")


@dataclass(frozen=True)
class ModelOutcome:
    answer: ModelAnswer | None
    # Why there is no answer: a gateway reason, or UNREADABLE.
    reason: str | None = None


def intake_text(message: Message, reading: Reading, kind: str) -> str:
    """The message as the model sees it. An email is its main headers and decoded body text,
    not its MIME source; a form or call post is its JSON as sent."""
    if kind != "email":
        return message.raw.decode("utf-8", "replace")
    # The rules read already passed the header budget, so this parse is bounded.
    headers = BytesHeaderParser(policy=policy.compat32).parsebytes(message.raw)
    lines = [f"{name}: {headers[name]}" for name in EMAIL_HEADERS if headers[name]]
    return "\n".join(lines) + "\n\n" + reading.text


def context_text(items: Sequence[str], teams: Mapping[str, str]) -> str:
    team_lines = ", ".join(f"{slug} ({name})" for slug, name in sorted(teams.items()))
    return f"Item list: {', '.join(items)}\nTeam list: {team_lines}"


def load_field_methods(path: Path = FIELD_METHODS) -> Mapping[str, str]:
    """The per-field methods the eval chose, or the combined default before there is a
    choice. A file that names a field or method the reader does not know is refused."""
    if not path.exists():
        return DEFAULT_METHODS
    data: Any = yaml.safe_load(path.read_text())
    methods = data.get("methods") if isinstance(data, dict) else None
    if not isinstance(methods, dict):
        raise ValueError(f"{path}: field_methods needs a 'methods' mapping")
    chosen = {str(k): str(v) for k, v in cast(dict[Any, Any], methods).items()}
    if set(chosen) != set(FIELDS) or not set(chosen.values()) <= set(METHODS):
        raise ValueError(f"{path}: field_methods must give one of {METHODS} for each of {FIELDS}")
    return chosen


class ModelReader:
    def __init__(
        self,
        gateway: Gateway,
        methods: Mapping[str, str] = DEFAULT_METHODS,
        detached: bool = False,
    ) -> None:
        self.gateway = gateway
        self.methods = methods
        # A detached reader reads messages that are never committed (the eval's clean
        # twins), so its calls are logged without a message id.
        self.is_detached = detached

    def detached(self) -> ModelReader:
        return ModelReader(self.gateway, self.methods, detached=True)

    def request(
        self,
        message: Message,
        kind: str,
        reading: Reading,
        items: Sequence[str],
        teams: Mapping[str, str],
    ) -> ModelRequest:
        return ModelRequest(
            org_id=message.org_id,
            message_id=None if self.is_detached else message.id,
            task=READ_MESSAGE,
            intake_text=intake_text(message, reading, kind),
            context=context_text(items, teams),
            schema=answer_schema(items, sorted(teams)),
            raw=message.raw,
        )

    def read(
        self,
        message: Message,
        kind: str,
        reading: Reading,
        items: Sequence[str],
        teams: Mapping[str, str],
    ) -> ModelOutcome:
        try:
            reply = self.gateway.call(self.request(message, kind, reading, items, teams))
        except ModelUnavailableError as error:
            return ModelOutcome(None, error.reason)
        failpoints.fire(FAILPOINT_DURING_MODEL_READ)
        answer = parse_answer(reply.content, items, sorted(teams))
        return ModelOutcome(answer, None if answer else UNREADABLE)
