"""Transcribing memos (F37, F39): the transcript fills the note and the audio goes, on
success, on failure, out of attempts, when reaped and after 30 minutes. A late or stale
finish writes nothing, renewal outlives a database error, the transcriber's heartbeat moves
during a long memo, and the main worker never takes a transcription."""

from __future__ import annotations

import sys
import time
import types
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from firstcall.board.notes import NOTE_MAX
from firstcall.cli import app
from firstcall.db import Contact, Heartbeat, Job, Note, VoiceAudio
from firstcall.pipeline import jobs, worker
from firstcall.pipeline.jobs import TRANSCRIBE, Claimed, claim, keep_lease, reap, run, run_one
from firstcall.pipeline.model_read import ModelReader
from firstcall.pipeline.worker import (
    TRANSCRIBER,
    expire_tick,
    healthy,
    process_name,
    transcribe_work,
)
from firstcall.settings import Settings
from firstcall.voice.job import expire_waiting, run_transcription
from tests.board_helpers import Board
from tests.conftest import FakeClock
from tests.model_fakes import FakeTransport, gateway
from tests.voice_helpers import TRANSCRIPT, UNUSABLE, FakeTranscriber, upload

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
SELLER = "seller@larkspur.example"


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_10AM
    return Board(sessions, clock)


def _memo(board: Board, action: str | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    """An owned lead with one waiting memo: (lead, note)."""
    seller = board.user(SELLER, teams={"north": "salesperson"})
    lead = board.lead("north")
    board.own(lead, seller, MONDAY_10AM)
    board.set_lead(lead, arrival_at=MONDAY_10AM, owned_since=MONDAY_10AM,
                   uncontacted_since=MONDAY_10AM)  # fmt: skip
    answer = upload(board.signed_in(SELLER), lead, action=action)
    assert answer.status_code == 201, answer.text
    return lead, uuid.UUID(answer.json()["note_id"])


def _claim(board: Board) -> Claimed:
    claimed = claim(board.sessions, board.clock, 60, 5, kinds=(TRANSCRIBE,), skip=())
    assert claimed is not None
    return claimed


def _transcribe(board: Board, transcriber: Any) -> str | None:
    return run_transcription(board.sessions, board.clock, _claim(board), 60, 5, transcriber)


def _note(board: Board, note_id: uuid.UUID) -> Note | None:
    with board.sessions() as session:
        return session.get(Note, note_id)


def _waiting(board: Board, note_id: uuid.UUID) -> Note:
    note = _note(board, note_id)
    assert note is not None
    return note


def _audio(board: Board, note_id: uuid.UUID) -> int:
    with board.sessions() as session:
        return len(session.scalars(select(VoiceAudio).where(VoiceAudio.note_id == note_id)).all())


def _jobs(board: Board, note_id: uuid.UUID) -> dict[str, tuple[str, int]]:
    with board.sessions() as session:
        found = session.scalars(select(Job).where(Job.note_id == note_id)).all()
        return {job.kind: (job.state, job.attempts) for job in found}


def _failed(board: Board, note_id: uuid.UUID) -> None:
    note = _note(board, note_id)
    assert note is not None
    assert (note.read_state, note.read_reason, note.text) == ("failed", "not_transcribed", "")
    assert _audio(board, note_id) == 0
    assert _jobs(board, note_id)[TRANSCRIBE][0] == "failed"
    assert "read_note" not in _jobs(board, note_id)


def test_a_transcript_fills_the_note_queues_its_read_and_the_audio_goes(board: Board) -> None:
    _, note_id = _memo(board)
    transcriber = FakeTranscriber()
    ran = transcribe_work(board.sessions, board.clock, Settings(env="test"), transcriber, True)
    assert ran == 1
    assert transcriber.calls[0][1] == "webm"
    note = _note(board, note_id)
    assert note is not None
    assert (note.text, note.read_state, note.source) == (TRANSCRIPT, "pending", "voice")
    assert _audio(board, note_id) == 0
    assert _jobs(board, note_id) == {TRANSCRIBE: ("done", 1), "read_note": ("queued", 0)}


def test_with_no_key_the_transcript_is_kept_and_needs_a_model_key(board: Board) -> None:
    lead, note_id = _memo(board, action="text")
    transcribe_work(board.sessions, board.clock, Settings(env="test"), FakeTranscriber(), True)
    transport = FakeTransport()
    reader = ModelReader(gateway(board.sessions, board.clock, transport, has_key=False))
    assert run_one(board.sessions, board.clock, reader=reader) is True
    assert transport.requests == []
    note = _note(board, note_id)
    assert note is not None
    assert (note.text, note.read_state, note.read_reason) == (TRANSCRIPT, "needs_model", "no_key")
    detail = board.signed_in(SELLER).get(f"/api/leads/{lead}")
    assert detail.status_code == 200
    shown = detail.json()
    assert [(n["text"], n["read_state"]) for n in shown["notes"]] == [(TRANSCRIPT, "needs_model")]
    assert [c["kind"] for c in shown["contacts"]] == ["text"]


@pytest.mark.parametrize(
    "transcriber",
    [
        FakeTranscriber(error=UNUSABLE),
        FakeTranscriber(text="   "),
        FakeTranscriber(text="word " * (NOTE_MAX // 5 + 1)),
    ],
    ids=["unusable", "no speech", "over NOTE_MAX"],
)
def test_audio_that_cannot_transcribe_fails_at_once(
    board: Board, transcriber: FakeTranscriber
) -> None:
    _, note_id = _memo(board)
    assert _transcribe(board, transcriber) == "failed"
    _failed(board, note_id)
    assert _jobs(board, note_id)[TRANSCRIBE] == ("failed", 1)
    assert claim(board.sessions, board.clock, 60, 5, kinds=(TRANSCRIBE,), skip=()) is None


def test_a_picked_contact_stays_when_transcription_fails(board: Board) -> None:
    lead, note_id = _memo(board, action="call")
    _transcribe(board, FakeTranscriber(error=UNUSABLE))
    _failed(board, note_id)
    with board.sessions() as session:
        contacts = session.scalars(select(Contact).where(Contact.lead_id == lead)).all()
        assert [(c.kind, c.note_id) for c in contacts] == [("call", note_id)]
    assert board.get(lead).first_contact_at == MONDAY_10AM


def test_other_errors_retry_until_attempts_run_out(board: Board) -> None:
    _, note_id = _memo(board)
    transcriber = FakeTranscriber(error=RuntimeError("model blew up"))
    for attempt in range(1, 6):
        assert _transcribe(board, transcriber) is None
        state = _jobs(board, note_id)[TRANSCRIBE]
        if attempt < 5:
            assert state == ("queued", attempt)
            assert _audio(board, note_id) == 1
            board.clock.advance(seconds=2**attempt)
    _failed(board, note_id)
    with board.sessions() as session:
        job = session.scalars(select(Job).where(Job.note_id == note_id)).one()
        assert job.last_error == "RuntimeError: model blew up"


def test_a_reaped_job_out_of_attempts_fails_its_note(board: Board) -> None:
    _, note_id = _memo(board)
    claimed = _claim(board)
    with board.sessions.begin() as session:
        session.execute(update(Job).where(Job.id == claimed.job_id).values(attempts=5))
    board.clock.advance(seconds=61)
    assert reap(board.sessions, board.clock) == 1
    _failed(board, note_id)


def test_a_queued_job_out_of_attempts_fails_when_the_transcriber_claims_it(board: Board) -> None:
    _, note_id = _memo(board)
    with board.sessions.begin() as session:
        session.execute(update(Job).where(Job.kind == TRANSCRIBE).values(attempts=5))
    assert claim(board.sessions, board.clock, 60, 5, kinds=(TRANSCRIBE,), skip=()) is None
    _failed(board, note_id)


def test_a_memo_still_waiting_after_30_minutes_fails_and_loses_its_audio(board: Board) -> None:
    _, note_id = _memo(board)
    board.clock.advance(minutes=29)
    _, younger = _memo_on_new_lead(board)
    assert expire_waiting(board.sessions, board.clock) == 0
    board.clock.advance(minutes=2)
    assert expire_tick(board.sessions, board.clock) == {"expired_memos": 1}
    _failed(board, note_id)
    assert _audio(board, younger) == 1
    # Its job is over, so no transcriber picks it up.
    claimed = _claim(board)
    assert claimed.note_id == younger


def _stale_and_younger(board: Board) -> tuple[uuid.UUID, uuid.UUID]:
    """A memo waiting 31 minutes and one waiting 2."""
    _, stale = _memo(board)
    board.clock.advance(minutes=29)
    _, younger = _memo_on_new_lead(board)
    board.clock.advance(minutes=2)
    return stale, younger


def test_one_scheduler_pass_expires_a_stale_memo_with_no_transcriber_running(
    board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale, younger = _stale_and_younger(board)

    def stop(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        worker.schedule(board.sessions, board.clock, Settings(env="test"))
    _failed(board, stale)
    assert _audio(board, younger) == 1
    note = _note(board, younger)
    assert note is not None
    assert note.read_state == "transcribing"


def test_firstcall_tick_expires_a_stale_memo(
    board: Board, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `firstcall tick` reads the real clock, so the memos are dated from it.
    board.clock.current = datetime.now(UTC) - timedelta(minutes=31)
    stale, younger = _stale_and_younger(board)
    monkeypatch.setenv("FIRSTCALL_DATABASE_URL", database_url)
    done = CliRunner().invoke(app, ["tick"])
    assert done.exit_code == 0, done.output
    assert "'expired_memos': 1" in done.output
    _failed(board, stale)
    assert _audio(board, younger) == 1


def _memo_on_new_lead(board: Board) -> tuple[uuid.UUID, uuid.UUID]:
    with board.sessions() as session:
        seller = session.scalars(select(Note.author_id)).first()
    lead = board.lead("north")
    board.set_lead(lead, state="owned", owner_id=seller, pool_since=None)
    answer = upload(board.signed_in(SELLER), lead)
    assert answer.status_code == 201, answer.text
    return lead, uuid.UUID(answer.json()["note_id"])


def test_expiry_also_ends_a_transcription_already_running(board: Board) -> None:
    _, note_id = _memo(board)

    def expire() -> None:
        board.clock.advance(minutes=31)
        assert expire_waiting(board.sessions, board.clock) == 1

    assert _transcribe(board, FakeTranscriber(during=expire)) == "skipped"
    _failed(board, note_id)


def test_a_failing_expiry_is_logged_and_the_scheduler_goes_on(board: Board) -> None:
    class Broken:
        def __call__(self) -> Any:
            raise OperationalError("select", {}, Exception("database gone"))

    broken: Any = Broken()
    assert expire_tick(broken, board.clock) == {"expired_memos": 0}


def test_a_finish_from_an_old_attempt_writes_nothing(board: Board) -> None:
    _, note_id = _memo(board)
    second: list[Claimed] = []

    def reaped_and_claimed_again() -> None:
        board.clock.advance(seconds=61)
        assert reap(board.sessions, board.clock) == 1
        second.append(_claim(board))

    first = FakeTranscriber(text="the first attempt's words", during=reaped_and_claimed_again)
    assert _transcribe(board, first) == "skipped"
    note = _note(board, note_id)
    assert note is not None
    assert (note.text, note.read_state) == ("", "transcribing")
    assert _audio(board, note_id) == 1
    assert _jobs(board, note_id) == {TRANSCRIBE: ("running", 2)}
    # The old attempt's error changes nothing either.
    jobs.retry_or_fail(board.sessions, board.clock, first_claim(second[0]), UNUSABLE, 5, True)
    assert _jobs(board, note_id) == {TRANSCRIBE: ("running", 2)}
    done = run_transcription(board.sessions, board.clock, second[0], 60, 5, FakeTranscriber())
    assert done == "transcribed"
    assert _waiting(board, note_id).text == TRANSCRIPT


def first_claim(later: Claimed) -> Claimed:
    return Claimed(later.job_id, later.message_id, later.attempt - 1, later.note_id, later.kind)


def test_a_finish_after_the_lead_is_deleted_writes_nothing(board: Board) -> None:
    lead, note_id = _memo(board)
    admin = board.user("admin@larkspur.example", roles=["admin"])

    def deleted() -> None:
        assert board.signed_in(admin.email).delete(f"/api/leads/{lead}").status_code == 204

    assert _transcribe(board, FakeTranscriber(during=deleted)) == "skipped"
    assert _note(board, note_id) is None
    with board.sessions() as session:
        assert session.scalars(select(VoiceAudio)).all() == []
        assert session.scalars(select(Job).where(Job.note_id.is_not(None))).all() == []


def test_delete_lead_removes_a_waiting_memos_audio(board: Board) -> None:
    lead, note_id = _memo(board)
    admin = board.user("admin@larkspur.example", roles=["admin"])
    assert board.signed_in(admin.email).delete(f"/api/leads/{lead}").status_code == 204
    assert _note(board, note_id) is None
    assert _audio(board, note_id) == 0
    assert _jobs(board, note_id) == {}


class FlakySessions:
    """A session factory whose next `fails` transactions raise, as a dropped connection
    would."""

    def __init__(self, real: sessionmaker[Session], fails: int) -> None:
        self.real = real
        self.fails = fails

    def begin(self) -> Any:
        if self.fails:
            self.fails -= 1
            raise OperationalError("update jobs", {}, Exception("connection lost"))
        return self.real.begin()


def test_lease_renewal_outlives_a_database_error(board: Board) -> None:
    _memo(board)
    claimed = _claim(board)
    board.clock.advance(seconds=30)
    renewed: list[int] = []
    flaky: Any = FlakySessions(board.sessions, fails=1)
    with keep_lease(flaky, board.clock, claimed, 0.3, lambda: renewed.append(1)):
        time.sleep(0.8)
    assert flaky.fails == 0
    assert renewed
    with board.sessions() as session:
        job = session.get_one(Job, claimed.job_id)
        assert job.lease_until == board.clock.now() + timedelta(seconds=0.3)


def test_the_transcribers_heartbeat_moves_during_a_long_memo(board: Board) -> None:
    _, note_id = _memo(board)
    seen: list[datetime] = []

    def beat_at() -> datetime:
        with board.sessions() as session:
            found = session.get(Heartbeat, process_name(TRANSCRIBER))
            assert found is not None
            return found.beat_at

    def long_memo() -> None:
        seen.append(beat_at())
        time.sleep(1.5)
        seen.append(beat_at())

    settings = Settings(env="test", job_lease_seconds=1)
    transcribe_work(board.sessions, board.clock, settings, FakeTranscriber(during=long_memo), True)
    assert seen[1] > seen[0]
    assert healthy(board.sessions, TRANSCRIBER) is True
    assert _waiting(board, note_id).read_state == "pending"


def test_the_main_worker_never_takes_a_transcription(board: Board) -> None:
    lead, note_id = _memo(board)
    client = board.signed_in(SELLER)
    assert client.post(f"/api/leads/{lead}/notes", json={"text": "Typed."}).status_code == 201
    main = claim(board.sessions, board.clock, 60, 5)
    assert main is not None
    assert main.kind == "read_note"
    assert claim(board.sessions, board.clock, 60, 5) is None
    voice = claim(board.sessions, board.clock, 60, 5, kinds=(TRANSCRIBE,), skip=())
    assert voice is not None
    assert (voice.kind, voice.note_id) == (TRANSCRIBE, note_id)
    with pytest.raises(ValueError, match="transcriber"):
        run(board.sessions, board.clock, voice, 60, 5)


def test_the_transcriber_takes_only_transcriptions(board: Board) -> None:
    lead, _ = _memo(board)
    board.signed_in(SELLER).post(f"/api/leads/{lead}/notes", json={"text": "Typed."})
    ran = transcribe_work(
        board.sessions, board.clock, Settings(env="test"), FakeTranscriber(), True
    )
    assert ran == 1
    with board.sessions() as session:
        typed = session.scalars(select(Note).where(Note.source == "typed")).one()
        job = session.scalars(select(Job).where(Job.note_id == typed.id)).one()
        assert (job.kind, job.state) == ("read_note", "queued")


def test_a_transcriber_without_faster_whisper_exits_before_claiming_a_memo(
    board: Board, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, note_id = _memo(board)
    monkeypatch.setenv("FIRSTCALL_DATABASE_URL", database_url)
    monkeypatch.setenv("FIRSTCALL_TRANSCRIBE_MODE", "local")
    # As in an environment synced without the voice group: the import fails.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    done = CliRunner().invoke(app, ["worker", "--kind", "transcribe", "--until-idle"])
    assert done.exit_code == 1
    assert "uv sync --group voice" in done.output
    assert "docker compose --profile voice up transcriber" in done.output
    assert _jobs(board, note_id)[TRANSCRIBE] == ("queued", 0)
    assert _audio(board, note_id) == 1
    assert _waiting(board, note_id).read_state == "transcribing"


def _fake_whisper(monkeypatch: pytest.MonkeyPatch, fails: Exception | None = None) -> None:
    """A faster_whisper module whose model loads, or raises `fails`."""

    class WhisperModel:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if fails is not None:
                raise fails

    module = types.ModuleType("faster_whisper")
    monkeypatch.setattr(module, "WhisperModel", WhisperModel, raising=False)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


def _refused_at_startup(
    board: Board, database_url: str, monkeypatch: pytest.MonkeyPatch, note_id: uuid.UUID
) -> str:
    """Runs the local transcriber command; it must exit 1 with the memo untouched."""
    monkeypatch.setenv("FIRSTCALL_DATABASE_URL", database_url)
    monkeypatch.setenv("FIRSTCALL_TRANSCRIBE_MODE", "local")
    done = CliRunner().invoke(app, ["worker", "--kind", "transcribe", "--until-idle"])
    assert done.exit_code == 1, done.output
    assert _jobs(board, note_id)[TRANSCRIBE] == ("queued", 0)
    assert _audio(board, note_id) == 1
    assert _waiting(board, note_id).read_state == "transcribing"
    return done.output


def test_a_transcriber_without_ffmpeg_exits_before_claiming_a_memo(
    board: Board, database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, note_id = _memo(board)
    _fake_whisper(monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path))
    said = _refused_at_startup(board, database_url, monkeypatch, note_id)
    assert "ffmpeg and ffprobe not found on PATH" in said
    assert "docker compose --profile voice up transcriber" in said


def test_a_transcriber_whose_model_cannot_load_exits_before_claiming_a_memo(
    board: Board, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, note_id = _memo(board)
    _fake_whisper(monkeypatch, fails=OSError("cannot reach huggingface.co"))
    said = _refused_at_startup(board, database_url, monkeypatch, note_id)
    assert "The Whisper model small.en could not be loaded" in said
    assert "OSError: cannot reach huggingface.co" in said


def _whisper_module(monkeypatch: pytest.MonkeyPatch, model: type) -> None:
    module = types.ModuleType("faster_whisper")
    monkeypatch.setattr(module, "WhisperModel", model, raising=False)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


class _SilentModel:
    """Loads, and transcribes anything to no segments."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def transcribe(self, audio: Any, **options: Any) -> tuple[list[Any], None]:
        return [], None


def test_a_transcriber_whose_ffmpeg_does_not_run_exits_before_claiming_a_memo(
    board: Board, database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, note_id = _memo(board)
    _whisper_module(monkeypatch, _SilentModel)
    for tool in ("ffmpeg", "ffprobe"):
        broken = tmp_path / tool
        broken.write_text("#!/bin/sh\necho 'cannot run here' >&2\nexit 1\n")
        broken.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    said = _refused_at_startup(board, database_url, monkeypatch, note_id)
    assert "ffmpeg could not decode a generated test clip" in said
    assert "cannot run here" in said


def test_a_transcriber_whose_voice_filter_fails_exits_before_claiming_a_memo(
    board: Board, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, note_id = _memo(board)

    class NoVoiceFilter(_SilentModel):
        def transcribe(self, audio: Any, **options: Any) -> tuple[list[Any], None]:
            assert options["vad_filter"] is True
            raise ImportError("onnxruntime is not installed")

    _whisper_module(monkeypatch, NoVoiceFilter)
    said = _refused_at_startup(board, database_url, monkeypatch, note_id)
    assert "A test transcription of a generated clip failed" in said
    assert "ImportError: onnxruntime is not installed" in said


def test_the_worker_command_runs_the_transcriber_in_replay_mode(
    board: Board, database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import hashlib
    import json

    from tests.voice_helpers import fixture

    _, note_id = _memo(board)
    transcripts = tmp_path / "transcripts.jsonl"
    key = hashlib.sha256(fixture("ok.webm")).hexdigest()
    transcripts.write_text(json.dumps({"sha256": key, "text": "Replayed words."}) + "\n")
    for name, value in (
        ("DATABASE_URL", database_url), ("TRANSCRIBE_MODE", "replay"),
        ("VOICE_TRANSCRIPTS", str(transcripts)),
    ):  # fmt: skip
        monkeypatch.setenv(f"FIRSTCALL_{name}", value)
    runner = CliRunner()
    done = runner.invoke(app, ["worker", "--kind", "transcribe", "--until-idle"])
    assert done.exit_code == 0, done.output
    assert _waiting(board, note_id).text == "Replayed words."
    refused = runner.invoke(app, ["worker", "--kind", "voice", "--until-idle"])
    assert refused.exit_code == 2


def test_a_claim_the_expiry_ended_runs_nothing(board: Board) -> None:
    _, note_id = _memo(board)
    claimed = _claim(board)
    board.clock.advance(minutes=31)
    assert expire_waiting(board.sessions, board.clock) == 1
    transcriber = FakeTranscriber()
    done = run_transcription(board.sessions, board.clock, claimed, 60, 5, transcriber)
    assert done == "skipped"
    assert transcriber.calls == []
    _failed(board, note_id)


def test_a_job_whose_audio_is_gone_fails_its_note(board: Board) -> None:
    _, note_id = _memo(board)
    with board.sessions.begin() as session:
        session.execute(delete(VoiceAudio))
    assert _transcribe(board, FakeTranscriber()) == "failed"
    _failed(board, note_id)
