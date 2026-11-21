"""Recording a voice memo (F36, F39): who may, the size, format and length limits checked
before anything is stored, the waiting caps, and a picked contact logged at once."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import MutableMapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import anyio
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board.notes import ORG_WAITING_SECONDS, USER_WAITING
from firstcall.db import Contact, Job, LeadEvent, Note, User, VoiceAudio
from firstcall.voice import audio
from tests.board_helpers import Board
from tests.conftest import FakeClock
from tests.voice_helpers import FIXTURES, asgi_upload, counts, fixture, upload

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
SELLER = "seller@larkspur.example"


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_10AM
    return Board(sessions, clock)


@pytest.fixture
def owned(board: Board) -> tuple[Any, uuid.UUID]:
    seller = board.user(SELLER, teams={"north": "salesperson"})
    lead = board.lead("north")
    board.own(lead, seller, MONDAY_10AM)
    board.set_lead(lead, arrival_at=MONDAY_10AM, owned_since=MONDAY_10AM,
                   uncontacted_since=MONDAY_10AM)  # fmt: skip
    return board.signed_in(SELLER), lead


@pytest.mark.parametrize("name", ["ok.webm", "ok.m4a", "ok.wav", "ok.mp3", "ok.ogg"])
def test_each_format_is_accepted_as_a_voice_note_waiting_for_its_transcript(
    board: Board, owned: tuple[Any, uuid.UUID], name: str
) -> None:
    client, lead = owned
    answer = upload(client, lead, name)
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert body["contact_id"] is None
    note_id = uuid.UUID(body["note_id"])
    with board.sessions() as session:
        note = session.get_one(Note, note_id)
        assert (note.source, note.text, note.read_state) == ("voice", "", "transcribing")
        stored = session.scalar(select(VoiceAudio).where(VoiceAudio.note_id == note_id))
        assert stored is not None
        assert stored.audio == fixture(name)
        assert stored.format == audio.sniff(fixture(name))
        assert 0.9 < float(stored.seconds) < 2.1
        kinds = session.scalars(select(Job.kind).where(Job.note_id == note_id)).all()
        assert kinds == ["transcribe"]
        added = session.scalars(select(LeadEvent).where(LeadEvent.type == "note.added")).one()
        assert added.payload == {"note_id": str(note_id), "by": str(note.author_id),
                                 "source": "voice"}  # fmt: skip
    shown = client.get(f"/api/leads/{lead}").json()["notes"]
    assert [(n["source"], n["read_state"], n["text"]) for n in shown] == [
        ("voice", "transcribing", "")
    ]


def _refused(board: Board, owned: tuple[Any, uuid.UUID], **kwargs: Any) -> Any:
    client, lead = owned
    before = counts(board)
    answer = upload(client, lead, **kwargs)
    assert counts(board) == before, "a refused memo stored something"
    return answer


@pytest.mark.parametrize(
    ("case", "kwargs"),
    [
        (
            "renamed text",
            {"body": b"Called Ana about Friday.\n" * 40, "content_type": "audio/webm"},
        ),
        ("webm with video", {"name": "video.webm"}),
        ("codec not allowed", {"name": "adpcm.wav", "content_type": "audio/wav"}),
        ("wrong declared type", {"name": "ok.webm", "content_type": "audio/mpeg"}),
        ("no declared type", {"name": "ok.webm", "content_type": "application/octet-stream"}),
        ("undecodable", {"body": b"\x1a\x45\xdf\xa3" + bytes(range(256)) * 20}),
        ("cut short", {"body": fixture("ok.mp3")[:200], "content_type": "audio/mpeg"}),
        ("no audio", {"body": fixture("ok.wav")[:44], "content_type": "audio/wav"}),
    ],
)
def test_a_bad_format_is_refused_with_415_and_nothing_stored(
    board: Board, owned: tuple[Any, uuid.UUID], case: str, kwargs: dict[str, Any]
) -> None:
    answer = _refused(board, owned, **kwargs)
    assert answer.status_code == 415, (case, answer.text)


def test_an_mp4_with_its_index_last_is_accepted_up_to_three_minutes(
    board: Board, owned: tuple[Any, uuid.UUID], tmp_path: Path
) -> None:
    """Safari and ffmpeg write an MP4's index (moov) after its audio; past ffmpeg's 32 KB
    read buffer, such a file decodes only from a seekable input."""
    memo = fixture("moov-last.m4a")
    assert memo.find(b"moov") > memo.find(b"mdat") > 0
    assert len(memo) > 32 * 1024
    client, lead = owned
    answer = upload(client, lead, "moov-last.m4a")
    assert answer.status_code == 201, answer.text
    with board.sessions() as session:
        stored = session.scalars(select(VoiceAudio)).one()
        assert 29.9 < float(stored.seconds) < 30.2
    # Six copies of it cut to 2:58, the index still last, remuxed with no encoder.
    longest = tmp_path / "longest.m4a"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-stream_loop", "5",
         "-i", str(FIXTURES / "moov-last.m4a"), "-c", "copy", "-t", "178", str(longest)],
        check=True,
    )  # fmt: skip
    data = longest.read_bytes()
    assert data.find(b"moov") > data.find(b"mdat") > 0
    assert 177 < audio.check(data, "audio/mp4").seconds < 178.2
    assert 177 < audio.seconds(audio.decode(data, "mp4")) < 178.2


def test_an_overlong_memo_is_refused_with_422_by_its_decoded_length(
    board: Board, owned: tuple[Any, uuid.UUID]
) -> None:
    assert _refused(board, owned, name="long.webm").status_code == 422
    # Its header claims 10 seconds; decoding finds 3:05.
    answer = _refused(board, owned, name="lying.webm")
    assert answer.status_code == 422
    assert answer.json() == {"detail": "a memo is at most 3 minutes"}


def test_a_memo_over_5_mb_is_refused_with_413(board: Board, owned: tuple[Any, uuid.UUID]) -> None:
    client, lead = owned
    before = counts(board)
    over = b"OggS" + bytes(audio.MAX_BYTES - 3)
    answer = upload(client, lead, body=over, content_type="audio/ogg")
    assert answer.status_code == 413

    def chunks() -> Any:
        yield over[: audio.MAX_BYTES // 2]
        yield over[audio.MAX_BYTES // 2 :]

    # No Content-Length: the read stops once the body passes the limit.
    headers = {"Content-Type": "audio/ogg"}
    streamed = client.post(f"/api/leads/{lead}/voice", content=chunks(), headers=headers)
    assert streamed.status_code == 413
    assert counts(board) == before


def test_a_declared_size_over_5_mb_is_refused_before_the_body_is_read(
    board: Board, owned: tuple[Any, uuid.UUID]
) -> None:
    client, lead = owned
    reads: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        reads.append(1)
        return {"type": "http.request", "body": b"OggS", "more_body": True}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    scope: dict[str, Any] = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "https", "server": ("testserver", 443), "client": ("127.0.0.1", 5000),
        "path": f"/api/leads/{lead}/voice", "raw_path": f"/api/leads/{lead}/voice".encode(),
        "root_path": "", "query_string": b"",
        "headers": [
            (b"host", b"testserver"), (b"content-type", b"audio/ogg"),
            (b"content-length", str(audio.MAX_BYTES + 1).encode()),
            (b"cookie", cookie.encode()),
            (b"x-csrf-token", client.headers["X-CSRF-Token"].encode()),
        ],
    }  # fmt: skip

    async def call() -> None:
        await board.app(scope, receive, send)

    anyio.run(call)
    assert sent[0]["status"] == 413
    assert reads == []


def test_a_picked_contact_is_logged_at_upload(board: Board, owned: tuple[Any, uuid.UUID]) -> None:
    client, lead = owned
    answer = upload(client, lead, action="call")
    assert answer.status_code == 201
    contact_id = uuid.UUID(answer.json()["contact_id"])
    with board.sessions() as session:
        contact = session.get_one(Contact, contact_id)
        note = session.get_one(Note, uuid.UUID(answer.json()["note_id"]))
        assert (contact.kind, contact.source, contact.note_id) == ("call", "typed", note.id)
        assert contact.at == MONDAY_10AM
    assert board.get(lead).first_contact_at == MONDAY_10AM
    assert board.get(lead).uncontacted_since is None


def test_an_action_is_checked_like_a_typed_notes(
    board: Board, owned: tuple[Any, uuid.UUID]
) -> None:
    client, lead = owned
    pool = board.lead("north")
    lead_client = board.signed_in(
        board.user("lead@larkspur.example", teams={"north": "team_lead"}).email
    )
    before = counts(board)
    assert upload(client, lead, action="fax").status_code == 422
    # A team lead may note a pool lead, but no one owns it, so no contact.
    assert upload(lead_client, pool, action="call").status_code == 422
    assert counts(board) == before
    assert upload(lead_client, pool).status_code == 201


def test_a_fourth_waiting_memo_from_one_person_is_refused_with_429(
    board: Board, owned: tuple[Any, uuid.UUID]
) -> None:
    client, lead = owned
    for _ in range(USER_WAITING):
        assert upload(client, lead).status_code == 201
    before = counts(board)
    answer = upload(client, lead)
    assert answer.status_code == 429
    assert counts(board) == before
    # Someone else on the team may still record.
    other = board.user("north3@larkspur.example", teams={"north": "salesperson"})
    second = board.lead("north")
    board.own(second, other, MONDAY_10AM)
    assert upload(board.signed_in(other.email), second).status_code == 201


def test_an_org_with_10_minutes_waiting_answers_503(
    board: Board, owned: tuple[Any, uuid.UUID]
) -> None:
    client, lead = owned
    assert upload(client, lead).status_code == 201
    with board.sessions.begin() as session:
        waiting = session.scalars(select(VoiceAudio)).one()
        waiting.seconds = Decimal(ORG_WAITING_SECONDS)
    before = counts(board)
    answer = upload(client, lead)
    assert answer.status_code == 503
    assert answer.json() == {"detail": "busy transcribing; try again shortly"}
    assert counts(board) == before
    with board.sessions.begin() as session:
        session.scalars(select(VoiceAudio)).one().seconds = Decimal(ORG_WAITING_SECONDS - 1)
    assert upload(client, lead).status_code == 201


def test_the_fixtures_stay_small() -> None:
    assert sum(p.stat().st_size for p in FIXTURES.iterdir()) < 400_000


def test_sniff_reads_each_formats_first_bytes() -> None:
    assert audio.sniff(fixture("ok.webm")) == "webm"
    assert audio.sniff(fixture("ok.m4a")) == "mp4"
    assert audio.sniff(fixture("ok.wav")) == "wav"
    assert audio.sniff(fixture("ok.ogg")) == "ogg"
    assert audio.sniff(fixture("ok.mp3")) == "mp3"
    assert audio.sniff(b"\xff\xfb\x90\x00") == "mp3"
    assert audio.sniff(b"RIFF\x00\x00\x00\x00AVI ") is None
    assert audio.sniff(b"") is None


def test_check_refuses_what_ffmpeg_cannot_finish_in_time(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))

    monkeypatch.setattr(audio.subprocess, "run", slow)
    with pytest.raises(audio.RefusedError) as refused:
        audio.check(fixture("ok.webm"), "audio/webm")
    assert refused.value.status == 415
    with pytest.raises(audio.UndecodableError, match="too long"):
        audio.decode(fixture("ok.webm"), "webm")


def test_check_refuses_an_oversize_body_it_is_handed() -> None:
    with pytest.raises(audio.RefusedError) as refused:
        audio.check(b"OggS" + bytes(audio.MAX_BYTES), "audio/ogg")
    assert refused.value.status == 413


def _owned_lead(board: Board) -> uuid.UUID:
    with board.sessions() as session:
        seller = session.scalars(select(User).where(User.email == SELLER)).one()
    lead = board.lead("north")
    board.own(lead, seller, MONDAY_10AM)
    return lead


def test_a_memo_over_a_waiting_cap_is_refused_before_its_body_is_read(
    board: Board, owned: tuple[Any, uuid.UUID]
) -> None:
    client, lead = owned
    for _ in range(3):
        assert upload(client, _owned_lead(board)).status_code == 201
    status, asked = asgi_upload(board, client, lead, [fixture("ok.webm")], "audio/webm")
    assert (status, asked) == (429, 0)
    other = board.user("north3@larkspur.example", teams={"north": "salesperson"})
    second = board.lead("north")
    board.own(second, other, MONDAY_10AM)
    with board.sessions.begin() as session:
        session.execute(update(VoiceAudio).values(seconds=Decimal(200)))
    status, asked = asgi_upload(
        board, board.signed_in(other.email), second, [fixture("ok.webm")], "audio/webm"
    )
    assert (status, asked) == (503, 0)


def test_uploads_past_the_checking_slots_are_refused_unread(
    board: Board, owned: tuple[Any, uuid.UUID]
) -> None:
    client, lead = owned
    held = [audio.CHECKS.acquire(blocking=False) for _ in range(audio.MAX_CHECKS)]
    assert all(held)
    try:
        status, asked = asgi_upload(board, client, lead, [fixture("ok.webm")], "audio/webm")
        assert (status, asked) == (503, 0)
    finally:
        for _ in held:
            audio.CHECKS.release()
    assert upload(client, lead).status_code == 201


def test_every_refusal_gives_its_checking_slot_back(
    board: Board, owned: tuple[Any, uuid.UUID]
) -> None:
    client, lead = owned
    assert upload(client, lead, "video.webm").status_code == 415
    assert upload(client, lead, "lying.webm").status_code == 422
    over = asgi_upload(board, client, lead, [b"OggS"], length=5_000_001)
    assert over == (413, 0)
    held = [audio.CHECKS.acquire(blocking=False) for _ in range(audio.MAX_CHECKS)]
    for taken in held:
        if taken:
            audio.CHECKS.release()
    assert all(held)
