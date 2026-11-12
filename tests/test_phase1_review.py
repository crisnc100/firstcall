"""Regression tests for the PR1b phase 1 review: HTML head text, the reread's call cap, an
identity the lead already holds, and one bad message in the reread."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core import read
from firstcall.core.read import Reference, SourceRules
from firstcall.db import Identity, Lead, Message
from firstcall.model import gateway as gw
from firstcall.model.transport import TransportError, TransportRequest
from firstcall.pipeline.model_read import ModelReader
from firstcall.pipeline.reread import MAX_CALLS_PER_MESSAGE, MAX_REREAD_ERRORS, reread
from firstcall.pipeline.worker import tick
from firstcall.settings import Settings
from tests.conftest import FakeClock
from tests.helpers import email
from tests.model_fakes import FakeTransport, answer, gateway
from tests.test_model_read import UNSEEN, ModelPipeline


@pytest.fixture(autouse=True)
def no_ambient_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FIRSTCALL_MODEL_"):
            monkeypatch.delenv(name)


@pytest.fixture
def pipe(sessions: sessionmaker[Session], clock: FakeClock) -> ModelPipeline:
    return ModelPipeline(sessions, clock)


def _fallback(pipe: ModelPipeline, raw: bytes = UNSEEN) -> Message:
    pipe.with_model(FakeTransport(), off=True)
    message = pipe.deliver("inbox", raw)
    assert message.needs_reread is True
    return message


# 2b32ab f1: head, title and style text never reaches the body text


HTML_FORM = (
    "From: Site <forms@larkspur.example>\nTo: leads@larkspur.example\nSubject: Contact\n"
    "MIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\n\n"
    "<html>\n<head><title>Contact form</title>\n<style>p { color: red }</style></head>\n"
    "<body>\nName: Ana Ruiz<br>\nPhone: (202) 555-0142<br>\n</body></html>\n"
)


def test_2b32ab_f1_head_title_and_style_stay_out_of_the_text() -> None:
    text = read._strip_tags(HTML_FORM.split("\n\n", 1)[1])
    lines = [line for line in text.split("\n") if line]
    assert lines == ["Name: Ana Ruiz", "Phone: (202) 555-0142"]


def test_2b32ab_f1_the_first_body_line_is_read_as_a_label() -> None:
    rules = SourceRules("email", relay_senders=frozenset({"forms@larkspur.example"}))
    reading = read.read(HTML_FORM.encode(), rules, Reference(("Juniper Mesa",), ()))
    assert (reading.name, reading.phone) == ("Ana Ruiz", "+12025550142")


def test_2b32ab_f1_an_unclosed_style_tag_keeps_the_text_after_it() -> None:
    text = read._strip_tags("<style>p { color: red }\nName: Ana Ruiz")
    assert "Name: Ana Ruiz" in text


# d33de7 f1 / ef71c1 f1: failed attempts do not use up a message's rereads


def test_d33de7_f1_an_outage_does_not_spend_the_reread_cap(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    message = _fallback(pipe)
    reader = ModelReader(gateway(sessions, clock, _down()))
    for _ in range(MAX_CALLS_PER_MESSAGE + 1):
        assert reread(sessions, reader) == {gw.DOWN: 1}
    back = ModelReader(gateway(sessions, clock, FakeTransport(default=answer(name="Ana Ruiz"))))
    assert reread(sessions, back) == {"filled": 1}
    assert pipe.message(message.id).needs_reread is False


def test_d33de7_f1_a_tick_stops_at_the_first_down(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    _fallback(pipe)
    _fallback(pipe, email("Bo Lin <bo@inbox.example>", "Copperleaf, please."))
    down = _down()
    assert reread(sessions, ModelReader(gateway(sessions, clock, down))) == {gw.DOWN: 1}
    assert len(down.requests) == 2


def _down() -> FakeTransport:
    def respond(request: TransportRequest) -> str:
        raise TransportError("503")

    return FakeTransport(respond=respond)


# ef71c1 f2: an identity the lead already holds


def test_ef71c1_f2_a_fill_the_lead_already_holds_as_an_identity_is_kept(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    message = _fallback(pipe)
    later = pipe.deliver(
        "inbox", email("Ana R <ana.ruiz@inbox.example>", "Still keen on Juniper Mesa. 202-555-0142")
    )
    assert later.lead_id == message.lead_id
    lead = pipe.lead(message.lead_id)
    assert lead.phone is None
    reader = ModelReader(
        gateway(sessions, clock, FakeTransport(default=answer(phone="202-555-0142")))
    )
    assert reread(sessions, reader) == {"filled": 1}
    assert pipe.lead(lead.id).phone == "+12025550142"
    with sessions() as session:
        rows = session.scalars(select(Identity).where(Identity.value == "+12025550142")).all()
        assert [r.lead_id for r in rows] == [lead.id]


# ef71c1 f3: one bad message never stops the reread or the scheduler


def _poison(text: str) -> FakeTransport:
    def respond(request: TransportRequest) -> str:
        if text in request.user:
            raise RuntimeError("bad answer path")
        return answer(name="Bo Lin")

    return FakeTransport(respond=respond)


def test_ef71c1_f3_one_bad_message_does_not_block_the_others(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    bad = _fallback(pipe)
    good = _fallback(pipe, email("<bo@inbox.example>", "Copperleaf, please."))
    transport = _poison("Who: Ana Ruiz")
    reader = ModelReader(gateway(sessions, clock, transport))
    assert tick(sessions, clock, Settings(), reader) == {
        "reaped": 0,
        "reread_error": 1,
        "reread_filled": 1,
    }
    assert pipe.lead(good.lead_id).name == "Bo Lin"
    failed = pipe.message(bad.id)
    assert failed.needs_reread is True
    assert failed.model_unavailable == "error: RuntimeError"
    for _ in range(MAX_REREAD_ERRORS + 2):
        tick(sessions, clock, Settings(), reader)
    poisoned = [r for r in transport.requests if "Who: Ana Ruiz" in r.user]
    assert len(poisoned) == MAX_REREAD_ERRORS


def test_ef71c1_f3_a_replay_miss_in_the_reread_is_contained(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock, tmp_path: Path
) -> None:
    message = _fallback(pipe)
    reader = ModelReader(gateway(sessions, clock, mode="replay", recordings=tmp_path))
    assert reread(sessions, reader) == {"halted": 1}
    contained = pipe.message(message.id)
    assert (contained.needs_reread, contained.reread_errors) == (True, 0)


def test_ef71c1_f3_an_error_leaves_the_lead_untouched(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    message = _fallback(pipe)
    with sessions.begin() as session:
        session.execute(update(Lead).where(Lead.id == message.lead_id).values(flags=["kept"]))
    reread(sessions, ModelReader(gateway(sessions, clock, _poison("Who: Ana Ruiz"))))
    lead = pipe.lead(message.lead_id)
    assert (lead.name, lead.flags) == (None, ["kept"])
