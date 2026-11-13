"""Lead events: append-only, each written in the same transaction as the change it records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firstcall.core.uuid7 import uuid7
from firstcall.db import Lead, LeadEvent


def write(
    session: Session, lead: Lead, event_type: str, cause: str, payload: dict[str, Any], at: datetime
) -> LeadEvent:
    """The next event in the lead's sequence. The caller holds the lead's row lock, so two
    writers never take the same number; the (lead, seq) key would refuse it anyway."""
    last = session.scalar(select(func.max(LeadEvent.seq)).where(LeadEvent.lead_id == lead.id))
    event = LeadEvent(
        id=uuid7(at),
        org_id=lead.org_id,
        lead_id=lead.id,
        seq=(last or 0) + 1,
        type=event_type,
        payload=payload,
        cause=cause,
    )
    session.add(event)
    return event
