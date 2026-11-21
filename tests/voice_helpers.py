"""Shared pieces of the voice tests: the audio fixtures, an upload, and a fake transcriber."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from firstcall.db import Contact, Job, LeadEvent, Note, VoiceAudio
from firstcall.voice.transcribe import UnusableAudioError
from tests.board_helpers import Board

FIXTURES = Path(__file__).parent / "fixtures" / "voice"
TYPES = {
    "ok.webm": "audio/webm;codecs=opus",
    "ok.m4a": "audio/mp4",
    "moov-last.m4a": "audio/mp4",
    "ok.wav": "audio/wav",
    "ok.mp3": "audio/mpeg",
    "ok.ogg": "audio/ogg;codecs=opus",
    "video.webm": "video/webm",
    "long.webm": "audio/webm",
    "lying.webm": "audio/webm",
}
TRANSCRIPT = "Called Ana this morning. Budget is around 450 thousand."


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def upload(
    client: Any,
    lead: uuid.UUID,
    name: str = "ok.webm",
    action: str | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
) -> Any:
    query = f"?action={action}" if action else ""
    return client.post(
        f"/api/leads/{lead}/voice{query}",
        content=fixture(name) if body is None else body,
        headers={"Content-Type": content_type or TYPES.get(name, "audio/webm")},
    )


def counts(board: Board) -> dict[str, int]:
    """How many rows an upload could add, to show a refusal stored nothing."""
    with board.sessions() as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model)) or 0
            for model in (Note, VoiceAudio, Job, Contact, LeadEvent)
        }


class FakeTranscriber:
    """Returns a fixed transcript, or raises; `during` runs while it "transcribes"."""

    def __init__(
        self,
        text: str = TRANSCRIPT,
        error: Exception | None = None,
        during: Callable[[], None] | None = None,
    ) -> None:
        self.text = text
        self.error = error
        self.during = during
        self.calls: list[tuple[int, str]] = []

    def transcribe(self, audio: bytes, fmt: str) -> str:
        self.calls.append((len(audio), fmt))
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error
        return self.text


UNUSABLE = UnusableAudioError("the audio does not decode")
