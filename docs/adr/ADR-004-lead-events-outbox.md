# ADR-004: Lead events as the outbox

- **Status:** Prediction committed 2026-11-11, before the pipeline or any delivery code.
- **Result:** pending.

## Context
Every change to a lead must reach the dashboard and, later, a CRM or webhook, exactly once
and in order. A separate message queue would add a service and a second place where a crash
can lose or repeat a change.

## Decision
A **lead event** is an append-only row written in the same database transaction as the change
it records. It has an event id (UUIDv7, so ids sort by time), the org, the lead, a per-lead
sequence number, a type and a payload. Each event is unique on (lead, cause), so reprocessing
a message adds nothing. Because the event commits with its change, the events table is the
outbox: webhook delivery later reads from it, and a crash after the commit loses nothing.

## Prediction (pre-registered)
1. A worker killed after the lead insert and before its job completes is picked up again,
   and the lead ends with exactly one `lead.created` event (fault suite against compose).
2. No lead in any eval run has a gap or a repeat in its sequence numbers.
3. When webhook delivery is built, a process killed after the commit and before delivery
   still delivers the event after restart, with no extra delivery code for recovery.
