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
