"""Shared pieces of the voice tests: the audio fixtures, an upload, and a fake transcriber."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, MutableMapping
from pathlib import Path
from typing import Any

import anyio
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


def asgi_upload(
    board: Board,
    client: Any,
    lead: uuid.UUID,
    chunks: Iterable[bytes],
    content_type: str = "audio/ogg",
    length: int | None = None,
) -> tuple[int, int]:
    """Posts a memo straight to the app, one ASGI body message per chunk, each saying more
    follows, with no Content-Length unless one is given. Returns the status and how many
    body messages the app asked for, so a test sees where the route stopped reading."""
    pending = iter(chunks)
    asked: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        asked.append(1)
        chunk = next(pending, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    headers = [
        (b"host", b"testserver"), (b"content-type", content_type.encode()),
        (b"cookie", "; ".join(f"{k}={v}" for k, v in client.cookies.items()).encode()),
        (b"x-csrf-token", client.headers["X-CSRF-Token"].encode()),
    ]  # fmt: skip
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    path = f"/api/leads/{lead}/voice"
    scope: dict[str, Any] = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "https", "server": ("testserver", 443), "client": ("127.0.0.1", 5000),
        "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "headers": headers,
    }  # fmt: skip

    async def call() -> None:
        await board.app(scope, receive, send)

    anyio.run(call)
    return sent[0]["status"], len(asked)


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
