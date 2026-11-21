# The board

The board is the web app where people see leads, claim them and get alerts. Words in bold
on first use, such as **pool stay**, are defined in [definitions.md](definitions.md).

## Who signs in

Each person signs in with an email and a password. A password needs at least 12
characters, and a user with no password cannot sign in. A session lasts
`FIRSTCALL_SESSION_DAYS` days (default 7). Setting a password ends the user's other
sessions. Sign-in allows `FIRSTCALL_SIGNIN_RATE_PER_MINUTE` tries a minute (default 10) per
client address and per email.

What each role may do is in the roles table in [definitions.md](definitions.md#who). The
pages each person gets follow from it:

| Who | Pages |
|---|---|
| A member of a team (salesperson or team lead) | Pool, My leads |
| Monitor or admin | Unassigned, Waiting longest, Not-a-lead tray |
| Admin | Users |
| Everyone | Inbox, Alerts on this device, Account |

A person can hold both a team role and an org role and then gets both sets of pages. An
admin adds people, sets their roles, teams and passwords, and deactivates them on the Users
page. Deactivating someone ends their sessions and sends their open leads back to their
pools. Removing someone from a team sends their open leads in that team back to its pool.

## Claiming

A new lead waits in its team's **pool**, and every member of the team is alerted. The
first person to press Claim gets the lead. A teammate who pressed at the same moment sees
"Already claimed by a teammate". Only an active member of the lead's team can claim it, so
a lead in Unassigned must be placed in a team first.

The owner can release a lead back to the pool. A team lead or admin can return it to the
pool, give it to another member of the team, or move it to another team.

Lists refresh every 15 seconds while the page is open in a visible tab.

## Wait timers and urgency labels

Every lead in a pool shows its **wait** and an **urgency label**: New, Waiting, Late or Very
late. The wait counts business minutes, the minutes inside the team's open hours, from the
start of the lead's current **pool stay**. A lead that goes back into a pool starts a new
stay, so a lead placed from Unassigned or rescued from the tray is not late the moment it
lands. The labels use the escalation clock and minutes, so a lead turns Late at the moment
its team leads are told. The exact minutes are under
[Speed to lead](definitions.md#speed-to-lead) and [Escalation](definitions.md#escalation).

**Time to claim** is different: it counts from arrival, not from the pool stay, and is set
once, by the first claim.

The Waiting longest page, for monitors and admins, lists every unclaimed lead that reads
Late or Very late, then the escalations that went out.

## Unassigned and the Not-a-lead tray

**Unassigned** holds leads the rules could not place in a team. Monitors are alerted when
one arrives, and a monitor or admin places it in a team. Placing starts a new pool stay;
time to claim keeps counting from arrival.

The **Not-a-lead tray** holds messages read as vendor, support or spam. A monitor or admin
can open one and rescue it. A rescue reads the stored message again, with the model's
stored answer and no new model call, then treats it as a lead: it creates a new lead or
attaches to the buyer's existing one. The lead's history records the rescue and who did it.

## Notes

The owner, a team lead of the lead's team or an admin can add a note to a lead. Saving a note
never waits on the model: the note is stored first, and the worker reads it in the
background. The lead page shows one of these under the note:

- **Reading this note…** while the read is queued or running.
- **Needs a model key** when no model could answer: no key, the model switched off or down, or
  the day's budget spent. The note stays as typed and is read later, once a model answers.
  Notes written before the reader existed start here.
- **Couldn't read this note** when the answer was not usable. The note stays as typed and is
  not read again.
- The **tidied note** and its **suggestions** when the read worked.

The lead page checks again every 15 seconds while a note is waiting to be read. It does not
redraw while a form on the page is being edited, so unsaved typing is never lost.

A read can update the buyer's profile (budget, authority, need, timeline). It never changes
a field a person set, and the profile form sends only the fields the person changed. The
merge rules are in [definitions.md](definitions.md#buyer-profile).

A **suggestion** is a call, text, email or meeting the note says already happened, with the
note's own words next to it. It logs nothing by itself. Confirm logs one contact, dated when
the note was written and made by the note's author. Dismiss logs none. A suggestion can be
closed once: a second click gets "already closed" (409). Confirm needs the lead to be owned or
in nurture. If the note itself already logged that kind of contact, the suggestion closes as
already logged. Deleting a lead deletes its suggestions and read jobs.

### Voice memos

Whoever may add a note may record a memo instead, and pick the contact it records, as on a
typed note. The memo is posted as the raw body to `POST /api/leads/{id}/voice?action=…`
with its type in `Content-Type`. Before anything is stored the api refuses:
- over 5 MB (413), read no further than that;
- anything but WebM, MP4, WAV, MP3 or Ogg audio, judged by the file's first bytes, sent under
  a type that does not fit them, or holding anything but one Opus, AAC, PCM, MP3 or Vorbis
  audio stream (415);
- over 3 minutes, measured by decoding it rather than trusting its header (422);
- a fourth memo from someone with three still waiting (429), or any memo while ten minutes of
  audio already wait in the org (503).

An accepted memo is a note with no text yet, waiting to be transcribed, and a picked contact
is logged at once. A separate transcriber process (`firstcall worker --kind transcribe`)
turns it into text with faster-whisper on the CPU, with no key (ADR-005); the main worker
never takes this work, so new-lead alerts never wait behind a memo. The transcript becomes the
note's text and is read like a typed note, so with no model key the note keeps the
transcript and says it needs one. A memo that cannot be transcribed (no speech, unreadable
audio, too long, or out of retries) fails, and the person records it again; a contact they
picked stays.

The audio is deleted when transcription ends, either way, and the scheduler fails any memo
still waiting 30 minutes after upload and deletes its audio, even when no transcriber runs.
Deleting a lead deletes its waiting audio too. Deleted means the row is gone at commit; the
bytes stay in Postgres's write-ahead log and backups until those turn over.

## Alerts

Alerts go to the in-app Inbox, by email, and by web push. Nothing is ever sent to a buyer.

| What happened | Who is alerted |
|---|---|
| A new lead | Everyone in its pool: the team's members, or the monitors for Unassigned |
| A lead went back into a pool, was placed or was moved | Everyone in the pool it entered |
| A lead was given to someone | The new owner |
| The buyer wrote again on an owned lead | The owner |
| An escalation fired | The people its level names |

**Email digests.** Email goes out as one digest per person and team, once the oldest
waiting alert in it is `FIRSTCALL_DIGEST_SECONDS` old (default 60). A digest that fails is
tried again after a wait that doubles each time; after `FIRSTCALL_EMAIL_RETRIES` retries
(default 3) its alerts are marked failed. `FIRSTCALL_EMAIL_BACKEND` picks where digests
go: `inbox` (the default) keeps them in the app's Inbox page, and `smtp` sends them to
`FIRSTCALL_SMTP_HOST` and `FIRSTCALL_SMTP_PORT`. The Inbox page shows only the plain-text
body, and its links to leads come from the server, never from the text.

**Checked again at send time.** Right before a push or a digest goes out, each alert is
checked against the person's access now: they must still be active and still able to see
the lead. Someone who leaves a team loses the pending alerts for that team's leads they can
no longer see; a monitor or admin still sees every lead, so theirs stay.

**The allowlist.** Email goes only to one plain address that belongs to an active user of
the org. It is refused when the address is a buyer's, meaning an identity or contact of any
lead, or when the part before the @ is a buyer's phone number, which is how email-to-text
gateways are addressed. A display name, a list of addresses, quotes, brackets or an encoded
word is refused too. The sender of a message in the Not-a-lead tray is not a buyer; once a
message is rescued, its sender is.

**Web push.** Push needs a VAPID key pair, `FIRSTCALL_VAPID_PRIVATE_KEY` and
`FIRSTCALL_VAPID_PUBLIC_KEY`. Without the private key, push is off and email still goes out.
Each person turns push on for a device on the Alerts on this device page; the browser asks
for permission only after they press the button. On an iPhone, Firstcall must first be
added to the Home Screen. The worker sends pushes right after each message it processes,
and the scheduler sends the ones escalation queues. Tapping a notification opens a page of
this app only.

**The push host list.** A browser gives each device an address on its vendor's push
service. Firstcall sends only to hosts on `FIRSTCALL_PUSH_HOSTS`, a JSON list such as
`'["fcm.googleapis.com", ".notify.windows.com"]'`. An entry that starts with "." matches any
subdomain of it. The default list holds the Chrome, Firefox, Apple and Windows push
services. An address must be https on port 443 to a host name on the list, never an IP
address. It is checked when a device subscribes and again before every send, and the send
goes to an address rebuilt from the checked parts. Each send waits at most 5 seconds and
reads at most 16 KB of the answer. A device whose push service answers 404 or 410 is
removed. `FIRSTCALL_PUSH_ALLOW_HTTP` lets tests use a fake push service over plain http;
it is refused when `FIRSTCALL_ENV=beta`.

**Links in alerts** point at `FIRSTCALL_BASE_URL`, the address people open the board at.
With `FIRSTCALL_ENV=beta`, it must be set to something other than the local default.

## Run the pages locally

The pages are built into the app image and served by the api at
<http://localhost:8020>. `/` is the public front page (`web/index.html`). The board is
`web/app.html`, which the api returns for every other page address, so it opens at
<http://localhost:8020/signin> and keeps its deep links.

```sh
docker compose up -d --wait
uv run firstcall seed
uv run firstcall user add you@example.com --name "Your Name" --role admin --role monitor
uv run firstcall user password you@example.com   # asks for the password, 12 or more characters
```

Sign in at <http://localhost:8020/signin> and add more people on the Users page. To add a
salesperson from the command line instead, give a team from `config/demo.yaml`:
`--team north=salesperson` (or `=team_lead`). After changing anything under `web/`, run
`docker compose up -d --build --wait` to rebuild the pages.

Compose leaves the email backend at `inbox`, so digests show up on the Inbox page. Push is
off: compose sets no VAPID key.
