// The one way the pages talk to the server: same-origin JSON under /api, the CSRF token on
// every write, and the server's clock read from each answer's Date header.

export interface Team {
  id: string;
  slug: string;
  name: string;
  my_role?: string | null;
}

export interface Lead {
  id: string;
  state: string;
  team: Team | null;
  owner: { id: string; name: string } | null;
  name: string | null;
  phone: string | null;
  email: string | null;
  item: string | null;
  arrival_at: string;
  pool_since: string | null;
  owned_since: string | null;
  claimed_at: string | null;
  claim_minutes: number | null;
  claim_business_minutes: number | null;
  possible_duplicates: string[];
  flags: unknown[];
  actions: string[];
  /** Notes, contacts, outcomes, reopen and delete this viewer may use on the lead. */
  work: string[];
  first_contact_at: string | null;
  first_contact_minutes: number | null;
  first_contact_business_minutes: number | null;
  /** Absent for a viewer who may not read the lead's notes; null when none is open. */
  followup?: Followup | null;
  wait_business_minutes: number | null;
  thresholds: { late: number; very_late: number } | null;
}

export interface MessageRow {
  id: string;
  received_at: string;
  source_time: string | null;
  source: string;
  status: string;
  reason: string | null;
  category: string | null;
  name: string | null;
  phone: string | null;
  email: string | null;
  item: string | null;
  text?: string;
}

export interface Followup {
  id: string;
  due_on: string;
  step: string;
  next_step: string;
  overdue: boolean;
}

export interface Note {
  id: string;
  text: string;
  author: string | null;
  created_at: string;
}

export interface ContactRow {
  id: string;
  note_id: string;
  kind: string;
  made_by: string | null;
  source: string;
  at: string;
}

export interface Profile {
  budget_text: string | null;
  budget_min: number | null;
  budget_max: number | null;
  authority: string;
  authority_others: string | null;
  need: string | null;
  timeline_text: string | null;
  timeline_month: string | null;
  notes: string | null;
  person_fields: string[];
}

export interface OutcomeRow {
  outcome: string;
  reason: string | null;
  reason_text: string | null;
  competitor: string | null;
  by: string | null;
  at: string;
}

export interface LeadDetail extends Lead {
  /** These four are absent for a viewer who may not read the lead's notes (a monitor). */
  notes?: Note[];
  contacts?: ContactRow[];
  profile?: Profile | null;
  outcomes?: OutcomeRow[];
  messages: MessageRow[];
  events: { seq: number; type: string; at: string; payload: Record<string, unknown> }[];
  people: Record<string, string>;
}

export interface User {
  id: string;
  email: string;
  name: string;
  roles: string[];
  active: boolean;
  teams: Record<string, string>;
}

export interface Session {
  user: User;
  csrf: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }
}

let csrf = "";
let skewMs = 0;
let current: Session | null = null;
let onSignedOut: (() => void) | null = null;

export function serverNow(): number {
  return Date.now() + skewMs;
}

export function whenSignedOut(handler: () => void): void {
  onSignedOut = handler;
}

export function session(): Session | null {
  return current;
}

function readClock(response: Response): void {
  const header = response.headers.get("Date");
  const at = header ? Date.parse(header) : Number.NaN;
  if (!Number.isNaN(at)) skewMs = at - Date.now();
}

async function send(method: string, path: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  readClock(response);
  return response;
}

async function detail(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    return typeof data.detail === "string" ? data.detail : response.statusText;
  } catch {
    return response.statusText;
  }
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response = await send(method, path, body);
  if (response.status === 403 && method !== "GET") {
    // The token may have rotated (a new sign-in in another tab); fetch it and try once more.
    if (await loadSession()) response = await send(method, path, body);
  }
  if (response.status === 401 && path !== "/session") {
    current = null;
    onSignedOut?.();
  }
  if (!response.ok) throw new ApiError(response.status, await detail(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function loadSession(): Promise<Session | null> {
  const response = await send("GET", "/session");
  if (!response.ok) {
    current = null;
    return null;
  }
  current = (await response.json()) as Session;
  csrf = current.csrf;
  return current;
}

export async function signIn(email: string, password: string): Promise<Session> {
  const fresh = await send("GET", "/csrf");
  csrf = ((await fresh.json()) as { csrf: string }).csrf;
  const response = await send("POST", "/session", { email, password });
  if (!response.ok) throw new ApiError(response.status, await detail(response));
  current = (await response.json()) as Session;
  csrf = current.csrf;
  return current;
}

/** Ends the session on the server. Throws when the server did not confirm, and then the
 * session stays: the cookie may still work, so the page must not act signed out. */
export async function signOut(): Promise<void> {
  try {
    await api("DELETE", "/session");
  } catch (error) {
    // 401: the server already has no session for this cookie, which is the goal.
    if (!(error instanceof ApiError && error.status === 401)) throw error;
  }
  current = null;
}
