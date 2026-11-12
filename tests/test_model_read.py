"""The model read in the pipeline, the no-model fallback and the reread job (F16)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core.read import read
from firstcall.db import Identity, Job, Lead, LeadEvent, Message, ModelCall, Source
from firstcall.model import gateway as gw
from firstcall.model.recordings import Recording, Recordings, request_key
from firstcall.model.transport import TransportError
from firstcall.pipeline.jobs import reap, run_one
from firstcall.pipeline.model_read import FAILPOINT_DURING_MODEL_READ, UNREADABLE, ModelReader
from firstcall.pipeline.process import org_view, source_rules
from firstcall.pipeline.reread import MAX_CALLS_PER_MESSAGE, reread
from firstcall.pipeline.worker import tick
from firstcall.settings import Settings
from tests.conftest import FakeClock
from tests.helpers import Pipeline, email
from tests.model_fakes import MODEL, FakeTransport, answer, gateway

ROOT = Path(__file__).resolve().parents[1]
# A vendor pitch with no spam signal: the rules read calls it a lead.
VENDOR = email(
    "Brightline Print <hello@brightline-print.example>",
    "We print yard signs for open houses. 20% off this month, call (202) 555-0177.",
    "Yard signs for your listings",
)
# A relay lead on a layout the rules do not know: they find the email, unsure, and no name.
UNSEEN = email(
    "Hearthly <leads@hearthly.example>",
    "Someone asked about Juniper Mesa.\nWho: Ana Ruiz / ana.ruiz@inbox.example",
    "Hearthly: new inquiry",
)


@dataclass
class ModelPipeline(Pipeline):
    reader: ModelReader | None = None

    def run(self) -> int:
        ran = 0
        while run_one(self.sessions, self.clock, reader=self.reader):
            ran += 1
        return ran

    def with_model(self, transport: FakeTransport, **overrides: Any) -> FakeTransport:
        self.reader = ModelReader(gateway(self.sessions, self.clock, transport, **overrides))
        return transport

    def calls(self) -> list[ModelCall]:
        with self.sessions() as session:
            return list(session.scalars(select(ModelCall)))


@pytest.fixture
def pipe(sessions: sessionmaker[Session], clock: FakeClock) -> ModelPipeline:
    return ModelPipeline(sessions, clock)


# The model read on the live path


def test_the_model_fills_what_the_rules_missed(pipe: ModelPipeline) -> None:
    transport = pipe.with_model(
        FakeTransport(default=answer(name="Ana Ruiz", email="ana.ruiz@inbox.example"))
    )
    message = pipe.deliver("inbox", UNSEEN)
    assert message.status == "lead_created"
    assert message.needs_reread is False
    assert message.model_unavailable is None
    assert message.model_answer is not None
    assert message.model_answer["name"] == "Ana Ruiz"
    lead = pipe.lead(message.lead_id)
    assert (lead.name, lead.email, lead.item) == (
        "Ana Ruiz",
        "ana.ruiz@inbox.example",
        "Juniper Mesa",
    )
    [call] = pipe.calls()
    assert (call.message_id, call.ok) == (message.id, True)
    [sent] = transport.requests
    assert "Who: Ana Ruiz / ana.ruiz@inbox.example" in sent.user
    assert "Ana Ruiz" not in sent.system


def test_a_form_post_reaches_the_model_as_its_json(pipe: ModelPipeline) -> None:
    transport = pipe.with_model(FakeTransport(default=answer(category="lead")))
    payload = {
        "name": "Ana Ruiz",
        "phone": "202-555-0142",
        "email": "",
        "listing": "",
        "message": "Hi",
    }
    message = pipe.deliver_form("site-form", payload)
    assert message.status == "lead_created"
    [sent] = transport.requests
    assert '"name": "Ana Ruiz"' in sent.user
    assert "Ana Ruiz" not in sent.system


def test_a_model_category_gates_an_unmatched_message(pipe: ModelPipeline) -> None:
    pipe.with_model(FakeTransport(default=answer(category="vendor")))
    message = pipe.deliver("inbox", VENDOR)
    assert (message.status, message.category) == ("not_a_lead", "vendor")
    assert pipe.leads() == []


def test_a_known_buyer_attaches_whatever_the_model_category(pipe: ModelPipeline) -> None:
    first = pipe.deliver("inbox", email("Ana Ruiz <ana@inbox.example>", "Juniper Mesa, please."))
    pipe.with_model(FakeTransport(default=answer(category="support")))
    second = pipe.deliver("inbox", email("Ana Ruiz <ana@inbox.example>", "Thanks for the call!"))
    assert (second.status, second.category) == ("attached", "support")
    assert second.lead_id == first.lead_id


def test_a_spam_signal_spends_no_model_call(pipe: ModelPipeline) -> None:
    transport = pipe.with_model(FakeTransport(default=answer()))
    message = pipe.deliver("inbox", email("Offers <win@prize-center.example>", "You won!"))
    assert message.status == "not_a_lead"
    assert message.needs_reread is False
    assert transport.requests == []


def test_a_replay_miss_fails_loudly_not_open(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock, tmp_path: Path
) -> None:
    pipe.reader = ModelReader(gateway(sessions, clock, mode="replay", recordings=tmp_path))
    message_id = pipe.store("inbox", VENDOR)
    pipe.run()
    message = pipe.message(message_id)
    assert message.status == "received"
    with sessions() as session:
        job = session.scalars(select(Job)).one()
    assert job.state == "queued"
    assert job.last_error is not None
    assert job.last_error.startswith("ReplayMissError")


# F16: no model means the rules path, failing open, marked for a reread


@pytest.mark.parametrize(
    ("overrides", "script", "reason"),
    [
        ({}, [TransportError("timeout"), TransportError("timeout")], gw.DOWN),
        ({"daily_cap_usd": Decimal("0.000001")}, [], gw.BUDGET),
        ({"off": True}, [], gw.KILL_SWITCH),
        ({"has_key": False}, [], gw.NO_KEY),
    ],
)
def test_without_the_model_the_lead_comes_from_the_rules_and_fails_open(
    pipe: ModelPipeline, overrides: dict[str, Any], script: list[TransportError], reason: str
) -> None:
    pipe.with_model(
        FakeTransport(script=list(script), default=answer(category="vendor")), **overrides
    )
    message = pipe.deliver("inbox", VENDOR)
    assert (message.status, message.category) == ("lead_created", "lead")
    assert message.needs_reread is True
    assert message.model_unavailable == reason
    lead = pipe.lead(message.lead_id)
    assert (lead.email, lead.phone) == ("hello@brightline-print.example", "+12025550177")
    assert [e.type for e in pipe.events(lead.id)] == ["lead.created"]


def test_an_unreadable_answer_takes_the_rules_path(pipe: ModelPipeline) -> None:
    pipe.with_model(FakeTransport(default="I think this is a vendor."))
    message = pipe.deliver("inbox", VENDOR)
    assert (message.status, message.category) == ("lead_created", "lead")
    assert (message.needs_reread, message.model_unavailable) == (True, UNREADABLE)


def test_offline_settings_take_the_rules_path(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    from firstcall.pipeline.worker import model_reader

    pipe = ModelPipeline(sessions, clock)
    pipe.reader = model_reader(sessions, clock, Settings())
    message = pipe.deliver("inbox", VENDOR)
    assert (message.status, message.needs_reread, message.model_unavailable) == (
        "lead_created",
        True,
        gw.OFFLINE,
    )


# The reread job


def _fallback_lead(pipe: ModelPipeline, raw: bytes = UNSEEN) -> tuple[Message, Lead]:
    pipe.with_model(FakeTransport(), off=True)
    message = pipe.deliver("inbox", raw)
    assert message.needs_reread is True
    return message, pipe.lead(message.lead_id)


def _edit_lead(sessions: sessionmaker[Session], lead_id: uuid.UUID, **values: object) -> None:
    with sessions.begin() as session:
        session.execute(update(Lead).where(Lead.id == lead_id).values(**values))


def test_the_reread_fills_only_empty_fields_and_never_moves_closes_or_overrides(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    message, lead = _fallback_lead(pipe)
    assert (lead.name, lead.phone, lead.email) == (None, None, "ana.ruiz@inbox.example")
    # A person owns the lead and has cleared its phone on purpose.
    _edit_lead(sessions, lead.id, state="owned", edited_fields=["phone"])
    before = pipe.lead(lead.id)

    reader = ModelReader(
        gateway(
            sessions,
            clock,
            FakeTransport(
                default=answer(
                    name="Ana Ruiz",
                    phone="202-555-0199",
                    email="someone.else@inbox.example",
                    item="Copperleaf",
                    category="spam",
                    team="east",
                )
            ),
        )
    )
    assert reread(sessions, reader) == {"filled": 1}

    after = pipe.lead(lead.id)
    assert after.name == "Ana Ruiz"
    assert after.phone is None
    assert after.email == "ana.ruiz@inbox.example"
    assert after.item == before.item == "Juniper Mesa"
    assert (after.state, after.team_id) == ("owned", before.team_id)
    assert after.flags == ["reread_not_a_lead:spam"]
    assert [e.type for e in pipe.events(lead.id)] == ["lead.created"]
    reread_message = pipe.message(message.id)
    assert reread_message.needs_reread is False
    assert reread_message.status == "lead_created"
    assert reread_message.model_answer is not None


def test_the_reread_never_takes_an_identity_another_lead_holds(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    other = pipe.deliver("inbox", email("Bo Lin <bo@inbox.example>", "Copperleaf\n202-555-0188"))
    _, lead = _fallback_lead(pipe)
    reader = ModelReader(
        gateway(sessions, clock, FakeTransport(default=answer(phone="202-555-0188")))
    )
    assert reread(sessions, reader) == {"unchanged": 2}
    assert pipe.lead(lead.id).phone is None
    with sessions() as session:
        holders = session.scalars(select(Identity.lead_id).where(Identity.value == "+12025550188"))
        assert list(holders) == [other.lead_id]


def test_the_reread_waits_while_the_model_is_unavailable(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    message, lead = _fallback_lead(pipe)
    transport = FakeTransport(default=answer(name="Ana Ruiz"))
    off = ModelReader(gateway(sessions, clock, transport, off=True))
    assert reread(sessions, off) == {}
    budget = ModelReader(gateway(sessions, clock, transport, daily_cap_usd=Decimal("0.000001")))
    assert reread(sessions, budget) == {gw.BUDGET: 1}
    assert transport.requests == []
    assert pipe.lead(lead.id).name is None
    assert pipe.message(message.id).needs_reread is True


def test_an_unreadable_reread_is_retried_a_bounded_number_of_times(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    message, _ = _fallback_lead(pipe)
    transport = FakeTransport(default="no idea")
    reader = ModelReader(gateway(sessions, clock, transport))
    for _ in range(MAX_CALLS_PER_MESSAGE + 2):
        reread(sessions, reader)
    assert len(transport.requests) == MAX_CALLS_PER_MESSAGE
    assert pipe.message(message.id).model_unavailable == UNREADABLE


def test_tick_runs_the_reread(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    _, lead = _fallback_lead(pipe)
    reader = ModelReader(gateway(sessions, clock, FakeTransport(default=answer(name="Ana Ruiz"))))
    assert tick(sessions, clock, Settings(), reader) == {"reaped": 0, "reread_filled": 1}
    assert pipe.lead(lead.id).name == "Ana Ruiz"
    assert tick(sessions, clock, Settings()) == {"reaped": 0}


# F16: a worker killed during the model read finishes on retry with one lead


def _record_answer(pipe: ModelPipeline, message_id: uuid.UUID, folder: Path, content: str) -> None:
    """A recorded answer for this message, so a replay-mode worker process can read it."""
    with pipe.sessions() as session:
        message = session.get_one(Message, message_id)
        source = session.get_one(Source, message.source_id)
        view = org_view(session, message.org_id, source)
        rules = read(message.raw, source_rules(source), view.reference)
        reader = ModelReader(gateway(pipe.sessions, pipe.clock, mode="replay", recordings=folder))
        outbound = reader.gateway.outbound(
            reader.request(message, source.kind, rules, view.reference.items, view.team_labels)
        )
    Recordings(folder).save(
        Recording(request_key(outbound), MODEL, content, 900, 80, "0.00105", 700)
    )


def _worker(
    database_url: str, folder: Path, failpoint: str = ""
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "FIRSTCALL_DATABASE_URL": database_url,
        "FIRSTCALL_MODEL_MODE": "replay",
        "FIRSTCALL_MODEL_NAME": MODEL,
        "FIRSTCALL_MODEL_RECORDINGS": str(folder),
        "FIRSTCALL_FAILPOINT": failpoint,
        "FIRSTCALL_JOB_LEASE_SECONDS": "60",
    }
    return subprocess.run(
        [sys.executable, "-m", "firstcall.cli", "worker", "--until-idle"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_worker_killed_during_the_model_read_finishes_on_retry_with_one_lead(
    pipe: ModelPipeline, sessions: sessionmaker[Session], database_url: str, tmp_path: Path
) -> None:
    message_id = pipe.store("inbox", UNSEEN)
    _record_answer(pipe, message_id, tmp_path, answer(name="Ana Ruiz"))

    killed = _worker(database_url, tmp_path, FAILPOINT_DURING_MODEL_READ)
    assert killed.returncode == 137, killed.stdout + killed.stderr
    assert f"FIRSTCALL_FAILPOINT fired {FAILPOINT_DURING_MODEL_READ}" in killed.stderr
    with sessions() as session:
        job = session.scalars(select(Job)).one()
        assert (job.state, job.attempts) == ("running", 1)
        assert session.get_one(Message, message_id).status == "received"
        assert session.scalars(select(Lead)).all() == []
        # The call made before the kill stays in the log.
        assert len(session.scalars(select(ModelCall)).all()) == 1

    later = FakeClock(datetime.now(UTC) + timedelta(hours=1))
    assert reap(sessions, later) == 1

    finished = _worker(database_url, tmp_path)
    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "FIRSTCALL_FAILPOINT fired" not in finished.stderr
    with sessions() as session:
        job = session.scalars(select(Job)).one()
        assert (job.state, job.attempts) == ("done", 2)
        message = session.get_one(Message, message_id)
        assert (message.status, message.needs_reread) == ("lead_created", False)
        [lead] = session.scalars(select(Lead)).all()
        assert lead.name == "Ana Ruiz"
        created = session.scalars(select(LeadEvent).where(LeadEvent.type == "lead.created")).all()
        assert len(created) == 1
        calls = session.scalars(select(ModelCall)).all()
        assert [(c.mode, c.message_id) for c in calls] == [("replay", message_id)] * 2
