/**
 * A voice call made on an agent's page, kept with that agent, not in an Arturo thread.
 *
 * The call's journal is the one every call already has: <data dir>/state/voice-calls/vc_<id>.json, written by the
 * voice side (the relay), never by the API. A call with an agent carries its id at the top level:
 *
 *   { "call_id": "vc_…", "agent": "<agent id>", "started_at": <epoch s>, "ended_at": <epoch s>|absent,
 *     "status": "live"|"ended", "turns": [{ "role": "user"|<the agent's>, "text": "…", "ts": <epoch s> }], … }
 *
 * A journal with no `agent` is Arturo's own call and is never listed here. The thumbs are the API's own, in
 * <data dir>/state/voice-call-ratings.json ({"calls": {"<call_id>": {"rating": "up"|"down", "by", "at"}}}), so the
 * journal keeps one writer.
 *
 * Work anchors: where an agent's work started during the call, so the call's transcript can link to it. For each
 * turn the person opened during the call, the first tool call of that turn and how many it ran, read from the
 * agent's own chat items.
 */
import fs from 'fs';
import path from 'path';
import { createHash } from 'crypto';

export const CALL_ID_RE = /^vc_[A-Za-z0-9_-]{4,64}$/;
export type Rating = 'up' | 'down';

export interface CallTurn { role: 'user' | 'agent'; text: string; ts: string | null }
export interface WorkAnchor { uuid: string; ts: string | null; steps: number }
export interface CallSummary {
  call_id: string; agent: string; status: 'live' | 'ended';
  started_at: string; ended_at: string | null; duration_s: number | null;
  turns: number; rating: Rating | null;
}
export interface CallRecord extends Omit<CallSummary, 'turns'> { turns: CallTurn[]; work: WorkAnchor[] }

export const callsDir = (dataDir: string) => path.join(dataDir, 'state', 'voice-calls');
const ratingsFile = (dataDir: string) => path.join(dataDir, 'state', 'voice-call-ratings.json');

const iso = (v: unknown): string | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN;
  if (Number.isFinite(n)) return new Date(n * 1000).toISOString();
  if (typeof v === 'string' && !Number.isNaN(Date.parse(v))) return new Date(Date.parse(v)).toISOString();
  return null;
};

// Parsed journals by file, reused while the file is unchanged: the chat polls every few seconds.
const cache = new Map<string, { sig: string; doc: any }>();

function readJournal(file: string): any | null {
  let st: fs.Stats;
  try { st = fs.statSync(file); } catch { cache.delete(file); return null; }
  const sig = `${st.ino}:${st.mtimeMs}:${st.size}`;
  const hit = cache.get(file);
  if (hit && hit.sig === sig) return hit.doc;
  let doc: any = null;
  try { doc = JSON.parse(fs.readFileSync(file, 'utf-8')); } catch { doc = null; }   // mid-write: next poll
  cache.set(file, { sig, doc });
  return doc;
}

function readRatings(dataDir: string): Record<string, { rating: Rating; by?: string; at?: string }> {
  try {
    const d = JSON.parse(fs.readFileSync(ratingsFile(dataDir), 'utf-8'));
    return d && typeof d.calls === 'object' && d.calls ? d.calls : {};
  } catch { return {}; }
}

function summary(doc: any, rating: Rating | null): CallSummary | null {
  if (!doc || typeof doc !== 'object' || !CALL_ID_RE.test(String(doc.call_id || ''))) return null;
  if (typeof doc.agent !== 'string' || !doc.agent) return null;
  const started = iso(doc.started_at);
  if (!started) return null;
  const ended = iso(doc.ended_at);
  const live = doc.status === 'live' && !ended;
  return {
    call_id: doc.call_id, agent: doc.agent, status: live ? 'live' : 'ended',
    started_at: started, ended_at: ended,
    duration_s: ended ? Math.max(0, Math.round((Date.parse(ended) - Date.parse(started)) / 1000)) : null,
    turns: Array.isArray(doc.turns) ? doc.turns.length : 0, rating,
  };
}

/** Every call with this agent, newest first. */
export function listAgentCalls(dataDir: string, agentId: string): CallSummary[] {
  let names: string[] = [];
  try { names = fs.readdirSync(callsDir(dataDir)).filter((n) => /^vc_[A-Za-z0-9_-]+\.json$/.test(n)); } catch { return []; }
  const ratings = readRatings(dataDir);
  const out: CallSummary[] = [];
  for (const n of names) {
    const doc = readJournal(path.join(callsDir(dataDir), n));
    if (!doc || doc.agent !== agentId) continue;
    const s = summary(doc, ratings[doc.call_id]?.rating ?? null);
    if (s) out.push(s);
  }
  return out.sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
}

function journalFor(dataDir: string, agentId: string, callId: string): any | null {
  if (!CALL_ID_RE.test(callId)) return null;
  const doc = readJournal(path.join(callsDir(dataDir), `${callId}.json`));
  return doc && doc.agent === agentId && doc.call_id === callId ? doc : null;
}

/** One call with this agent: its turns as You / the agent, and where its work started. Null when the call is not
 *  this agent's (another agent's, or Arturo's own). */
export function getAgentCall(dataDir: string, agentId: string, callId: string, chatItems: readonly any[] = []): CallRecord | null {
  const doc = journalFor(dataDir, agentId, callId);
  if (!doc) return null;
  const s = summary(doc, readRatings(dataDir)[callId]?.rating ?? null);
  if (!s) return null;
  const turns: CallTurn[] = (Array.isArray(doc.turns) ? doc.turns : [])
    .filter((t: any) => t && typeof t.text === 'string' && t.text.trim())
    // A tool run or a system line is not something either of them said.
    .filter((t: any) => t.role !== 'tool' && t.role !== 'system')
    .map((t: any) => ({ role: t.role === 'user' ? 'user' : 'agent', text: t.text, ts: iso(t.ts) }));
  return { ...s, turns, work: callWorkAnchors(chatItems, s.started_at, s.ended_at) };
}

/** Save or clear (null) a person's thumbs for one of this agent's calls. False when the call is not this agent's. */
export function setCallRating(dataDir: string, agentId: string, callId: string, rating: Rating | null,
                              by: string | null, now: () => Date = () => new Date()): boolean {
  if (!journalFor(dataDir, agentId, callId)) return false;
  const all = readRatings(dataDir);
  if (rating) all[callId] = { rating, ...(by ? { by } : {}), at: now().toISOString() };
  else delete all[callId];
  const f = ratingsFile(dataDir);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ calls: all }, null, 1));
  fs.renameSync(tmp, f);
  return true;
}

const isPersonTurn = (it: any) => it?.kind === 'text' && it.role === 'user' && !it.is_system && !it.machine
  && typeof it.text === 'string' && it.text.trim();

/** For each turn the person opened between `startIso` and `endIso` (open while the call is live): the first tool call
 *  of the turn (the spot the work started) and how many tool calls the turn ran. A turn with no tool call has none. */
export function callWorkAnchors(items: readonly any[], startIso: string, endIso: string | null): WorkAnchor[] {
  const from = Date.parse(startIso);
  const to = endIso ? Date.parse(endIso) : Number.POSITIVE_INFINITY;
  const out: WorkAnchor[] = [];
  let cur: WorkAnchor | null = null;
  let open = false;
  for (const it of items) {
    if (isPersonTurn(it)) {
      if (cur) out.push(cur);
      cur = null;
      const t = Date.parse(String(it.ts ?? ''));
      open = !Number.isNaN(t) && t >= from && t <= to;
      continue;
    }
    if (!open || it?.kind !== 'tool_use' || it.parent_tool_use_id) continue;
    if (!cur) cur = { uuid: String(it.uuid ?? it.id ?? ''), ts: typeof it.ts === 'string' ? it.ts : null, steps: 0 };
    cur.steps += 1;
  }
  if (cur) out.push(cur);
  return out.filter((a) => a.uuid);
}

/** The marker line the chat already draws as a call card ("Voice chat · mm:ss", the thread kit's
 *  `[voice-call: <id> <path>]` grammar); the card reads the call by its id, never by the path. */
export function callMarkerText(dataDir: string, callId: string): string {
  return `[voice-call: ${callId} ${path.join(callsDir(dataDir), `${callId}.json`)}]`;
}

/** This agent's ended calls placed into a window of chat items by when each started, as the chat's own call card: a
 *  user-row text item and its render node, the row the chat already draws a `[voice-call:]` marker on as the card.
 *  Merged after the window's audience tags are set, so a call never takes over a turn's closing reply. Range as the compaction notices': only calls at or after `from` and before `to`.
 *  No uuid on purpose: a client never takes one for a paging cursor (it is not a line of the transcript). */
export function mergeCallMarkers(body: { items: any[]; render_items: any[] }, calls: readonly CallSummary[], dataDir: string,
                                 range: { from: string | null; to: string | null }): { items: any[]; render_items: any[] } {
  const ms = (v: string | null) => (v ? Date.parse(v) : NaN);
  const from = ms(range.from), to = ms(range.to);
  const pick = calls.filter((c) => c.status === 'ended').filter((c) => {
    const t = Date.parse(c.started_at);
    return (Number.isNaN(from) || t >= from) && (Number.isNaN(to) || t < to);
  });
  if (!pick.length) return body;
  const place = (list: any[], node: any, t: number) => {
    let at = list.length;
    for (let i = 0; i < list.length; i++) {
      const it = Date.parse(String(list[i]?.ts ?? ''));
      if (!Number.isNaN(it) && it > t) { at = i; break; }
    }
    list.splice(at, 0, node);
  };
  const items = body.items.slice();
  const render = body.render_items.slice();
  for (const c of [...pick].sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at))) {
    const text = callMarkerText(dataDir, c.call_id);
    const extra = { call_id: c.call_id, duration_s: c.duration_s, rating: c.rating, audience: 'user' };
    const t = Date.parse(c.started_at);
    place(items, { kind: 'text', role: 'user', text, ts: c.started_at, ...extra }, t);
    place(render, { kind: 'user', text, ts: c.started_at, key: `call:${c.call_id}`, ...extra }, t);
  }
  return { ...body, items, render_items: render };
}

// ── Hand-off: the call, given to the agent as its own words ─────────────────────────────────────────────────────────
//
// When a call on an agent's page ends, the dashboard asks for a hand-off and the API sends the agent ONE message:
// the call's transcript inside <voice-call-transcript>, with an instruction to treat everything the voice said as
// its own words and carry on in text. The chat hides the tag and draws the call's card there instead
// (handoffCallId / handedOffCalls), so the card sits where the call ended, and is not also placed where it started.
// Handed-off calls are kept in <data dir>/state/voice-call-handoffs.json ({"calls": {"<call_id>": {agent, at, by,
// state}}}), the API's own file: a second hand-off of the same call is a no-op.

export const HANDOFF_WINDOW_MS = 10 * 60 * 1000;
export const CLIENT_CALL_ID_RE = /^vc_client_[0-9a-f]{12}$/;
const handoffsFile = (dataDir: string) => path.join(dataDir, 'state', 'voice-call-handoffs.json');

export const HANDOFF_INSTRUCTION = 'This was your own voice call with the person just now. Everything the voice said '
  + 'below was you speaking. Don\'t treat it as outside context, and don\'t mention markers, tags or files. Continue '
  + 'naturally in text: acknowledge anything left open and act on what they asked. Work already sent to you during '
  + 'the call (a "[Voice call]:" message) is already in hand: don\'t start it a second time.';

/** The id the browser knows a relay call by: vc_client_<sha1(conversation id)[:12]> (relay_live_bridge.py). The
 *  server's journal carries the conversation id (`conv_id`), so the browser's id is found from it. */
export function clientCallIdOf(doc: any): string | null {
  const conv = typeof doc?.conv_id === 'string' ? doc.conv_id : '';
  return conv ? `vc_client_${createHash('sha1').update(conv).digest('hex').slice(0, 12)}` : null;
}

export interface HandoffEntry { agent: string; at: string; by?: string | null; state: string }

export function readHandoffs(dataDir: string): Record<string, HandoffEntry> {
  try {
    const d = JSON.parse(fs.readFileSync(handoffsFile(dataDir), 'utf-8'));
    return d && typeof d.calls === 'object' && d.calls ? d.calls : {};
  } catch { return {}; }
}

function writeHandoffs(dataDir: string, all: Record<string, HandoffEntry>): void {
  const f = handoffsFile(dataDir);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ calls: all }, null, 1));
  fs.renameSync(tmp, f);
}

/** Record (or, with null, forget) a call's hand-off. Synchronous: a check and its record never interleave. */
export function markHandoff(dataDir: string, callId: string, entry: HandoffEntry | null): void {
  const all = readHandoffs(dataDir);
  if (entry) all[callId] = entry; else delete all[callId];
  writeHandoffs(dataDir, all);
}

/** The call a hand-off is for: this agent's calls that ended within HANDOFF_WINDOW_MS of `now`; the one the browser's
 *  id names when it names one of them, else the newest. Null when none ended that recently. */
export function findHandoffCall(dataDir: string, agentId: string, clientCallId: string | null, now: number = Date.now()):
    { call: CallSummary; doc: any; matched: 'client_id' | 'latest' } | null {
  const recent = listAgentCalls(dataDir, agentId).filter((c) => c.status === 'ended' && c.ended_at
    && now - Date.parse(c.ended_at) <= HANDOFF_WINDOW_MS && Date.parse(c.ended_at) <= now + 60_000);
  if (!recent.length) return null;
  const docOf = (c: CallSummary) => journalFor(dataDir, agentId, c.call_id);
  if (clientCallId && CLIENT_CALL_ID_RE.test(clientCallId)) {
    for (const c of recent) {
      const doc = docOf(c);
      if (doc && clientCallIdOf(doc) === clientCallId) return { call: c, doc, matched: 'client_id' };
    }
  }
  const newest = [...recent].sort((a, b) => Date.parse(b.ended_at!) - Date.parse(a.ended_at!))[0];
  const doc = docOf(newest);
  return doc ? { call: newest, doc, matched: 'latest' } : null;
}

const mmss = (s: number | null) => {
  const n = Math.max(0, Math.round(s ?? 0));
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
};

/** The one message the agent gets: the call as its own words, then carry on. A turn cannot close the tag early. */
export function handoffMessage(call: CallSummary, doc: any, agentName: string): string {
  const name = String(agentName || call.agent).replace(/[\r\n:<>]+/g, ' ').trim() || call.agent;
  const clean = (t: string) => t.replace(/<\/?\s*voice-call-transcript[^>]*>/gi, '').replace(/\s+/g, ' ').trim();
  const lines = (Array.isArray(doc?.turns) ? doc.turns : [])
    .filter((t: any) => t && typeof t.text === 'string' && t.text.trim() && t.role !== 'tool' && t.role !== 'system')
    .map((t: any) => `${t.role === 'user' ? 'You' : name}: ${clean(t.text)}`)
    .filter((l: string) => !/:\s*$/.test(l));
  return `<voice-call-transcript id="${call.call_id}" duration="${mmss(call.duration_s)}">\n${HANDOFF_INSTRUCTION}\n\n`
    + `${lines.join('\n')}\n</voice-call-transcript>`;
}

const HANDOFF_TAG = /<voice-call-transcript\s+id="(vc_[A-Za-z0-9_-]{4,64})"[^>]*>[\s\S]*?<\/voice-call-transcript>/;

/** The call a chat line hands off, when the line is a hand-off message; else null. */
export function handoffCallId(text: string): string | null {
  return typeof text === 'string' && text.includes('<voice-call-transcript') ? HANDOFF_TAG.exec(text)?.[1] ?? null : null;
}

/** The line as the chat shows it: the tag gone, the call's card marker in its place (the row the chat draws as the
 *  card), and any words around it kept. */
export function showHandoff(text: string, dataDir: string): { text: string; call_id: string } | null {
  const id = handoffCallId(text);
  if (!id) return null;
  const rest = text.replace(HANDOFF_TAG, '').trim();
  return { text: [callMarkerText(dataDir, id), rest].filter(Boolean).join('\n'), call_id: id };
}
