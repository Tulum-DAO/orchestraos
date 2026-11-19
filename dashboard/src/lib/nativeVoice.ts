/**
 * nativeVoice.ts — the pure half of the native GPT-Live call (the iPhone app's engine), reached through
 * /api/voice/native (api/src/routes/voice-native.ts) -> gateway /arturo/gptlive/ws -> arturo native.
 * No DOM, no sockets: frame encode/decode, the mic framer + resampler, the transcript row reducer, the tools merge,
 * engine choice and close-code wording. lib/nativeVoiceSession.ts is the browser glue around these.
 *
 * Wire (authoritative contract from the native server owner):
 *   up    TEXT {"type":"start","settle":"fast"} right after open; BINARY 16 kHz mono s16le, 100 ms = 3200 bytes,
 *         continuously (muted = zero-filled frames of the same size); TEXT {"type":"context",page,agent?} after
 *         `state` and on route change (no exact repeats); TEXT {"type":"disconnect"} to hang up.
 *   down  BINARY Arturo's voice, 16 kHz mono s16le, already paced; TEXT state | tools | transcript | audio_end |
 *         barge | tick | notice | refused | stopped (unknown types ignored).
 */

export const NATIVE_RATE = 16000;
export const NATIVE_FRAME_SAMPLES = 1600;                 // 100 ms at 16 kHz
export const NATIVE_FRAME_BYTES = NATIVE_FRAME_SAMPLES * 2; // 3200

// ── up: text frames ──────────────────────────────────────────────────────────────────────────────────────────────

export const START_FRAME = JSON.stringify({ type: 'start', settle: 'fast' });
export const DISCONNECT_FRAME = JSON.stringify({ type: 'disconnect' });

export type NativePage = 'Approvals' | 'Agents' | 'Arturo' | 'Projects' | 'Pipeline';
const AGENT_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

/** The page name the native server keeps for a dashboard route, and the agent when the route is one agent's page.
 *  null: a page the server would drop the whole frame for, so nothing is sent. */
export function pageForRoute(route: string): { page: NativePage; agent?: string } | null {
  const seg = (route || '/').split('?')[0].split('/').filter(Boolean);
  const head = seg[0] || '';
  if (head === '' || head === 'arturo') return { page: 'Arturo' };
  if (head === 'approvals') return { page: 'Approvals' };
  if (head === 'agents') return { page: 'Agents' };
  if (head === 'agent') {
    let id = '';
    try { id = decodeURIComponent(seg[1] || ''); } catch { id = ''; }
    return AGENT_RE.test(id) ? { page: 'Agents', agent: id } : { page: 'Agents' };
  }
  if (head === 'projects' || head === 'roadmaps') return { page: 'Projects' };
  if (head === 'pipeline') return { page: 'Pipeline' };
  return null;
}

/** {"type":"context","page",agent?} for a route, or null (unmapped page). A browser sends no `device`. */
export function contextFrame(route: string): string | null {
  const p = pageForRoute(route);
  if (!p) return null;
  return JSON.stringify(p.agent ? { type: 'context', page: p.page, agent: p.agent } : { type: 'context', page: p.page });
}

// ── up: mic audio ────────────────────────────────────────────────────────────────────────────────────────────────

/** Streaming box-filter downsampler (any rate >= 16 kHz -> 16 kHz) that keeps its fractional position across chunks,
 *  so 128-sample worklet blocks do not drift or click the way per-chunk rounding would. */
export class StreamDownsampler {
  private readonly ratio: number;
  private carry: number[] = [];
  private pos = 0;                 // where the next output sample starts, in input samples, relative to carry[0]
  readonly inRate: number;
  readonly outRate: number;
  constructor(inRate: number, outRate = NATIVE_RATE) {
    this.inRate = inRate;
    this.outRate = outRate;
    if (!(inRate > 0) || !(outRate > 0)) throw new Error('StreamDownsampler: bad rate');
    if (outRate > inRate) throw new Error(`StreamDownsampler: cannot upsample ${inRate} -> ${outRate}`);
    this.ratio = inRate / outRate;
  }
  push(input: Float32Array): Float32Array {
    if (this.ratio === 1) return input.slice();
    const buf = this.carry.length ? Float32Array.from([...this.carry, ...input]) : input;
    const out: number[] = [];
    for (;;) {
      const a = Math.floor(this.pos);
      const b = Math.floor(this.pos + this.ratio);
      if (b > buf.length) break;
      let sum = 0;
      for (let i = a; i < b; i++) sum += buf[i];
      out.push(b > a ? sum / (b - a) : buf[a] ?? 0);
      this.pos += this.ratio;
    }
    const keepFrom = Math.floor(this.pos);
    this.carry = Array.from(buf.subarray(keepFrom));
    this.pos -= keepFrom;
    return Float32Array.from(out);
  }
}

export function floatToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/** Int16 samples -> exact 3200-byte little-endian frames; the remainder waits for the next push. */
export class PcmFramer {
  private pending = new Int16Array(NATIVE_FRAME_SAMPLES);
  private fill = 0;
  push(samples: Int16Array): ArrayBuffer[] {
    const frames: ArrayBuffer[] = [];
    let i = 0;
    while (i < samples.length) {
      const n = Math.min(NATIVE_FRAME_SAMPLES - this.fill, samples.length - i);
      this.pending.set(samples.subarray(i, i + n), this.fill);
      this.fill += n;
      i += n;
      if (this.fill === NATIVE_FRAME_SAMPLES) {
        frames.push(int16ToLeBytes(this.pending));
        this.fill = 0;
      }
    }
    return frames;
  }
}

/** Little-endian bytes whatever the host's endianness (a DataView, not the Int16Array's own buffer). */
export function int16ToLeBytes(s: Int16Array): ArrayBuffer {
  const out = new ArrayBuffer(s.length * 2);
  const v = new DataView(out);
  for (let i = 0; i < s.length; i++) v.setInt16(i * 2, s[i], true);
  return out;
}

/** 16 kHz s16le bytes -> Float32 [-1, 1) for an AudioBuffer. An odd trailing byte is dropped. */
export function leBytesToFloat(buf: ArrayBuffer): Float32Array<ArrayBuffer> {
  const n = Math.floor(buf.byteLength / 2);
  const v = new DataView(buf);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = v.getInt16(i * 2, true) / 0x8000;
  return out;
}

/** Mic chunks (any rate) -> 3200-byte frames; muted chunks become the same number of zero frames, never a gap. */
export class MicUplink {
  private readonly ds: StreamDownsampler;
  private readonly framer = new PcmFramer();
  constructor(inRate: number) { this.ds = new StreamDownsampler(inRate); }
  push(input: Float32Array, muted: boolean): ArrayBuffer[] {
    const down = this.ds.push(input);
    return this.framer.push(muted ? new Int16Array(down.length) : floatToInt16(down));
  }
}

// ── down: text frames ────────────────────────────────────────────────────────────────────────────────────────────

export interface NativeToolCall { call_id: string; name: string; args?: unknown; ok?: boolean; dup_hits?: number; task_id?: string | null }
export type NativeFrame =
  | { type: 'state'; mode?: string; conversation_id?: string; principal?: string; caps?: unknown }
  | { type: 'tools'; calls: NativeToolCall[]; refused: unknown[]; claims_blocked: unknown[] }
  | { type: 'transcript'; who: 'user' | 'arturo'; text: string; new: boolean; withdrawn?: boolean }
  | { type: 'audio_end' }
  | { type: 'barge'; cut: boolean }
  | { type: 'tick'; elapsed_s?: number; spent_usd?: number }
  | { type: 'notice'; text: string; code?: string }
  | { type: 'refused'; reason: string }
  | { type: 'stopped'; reason?: string; report?: unknown };

function spentUsd(cost: unknown): number | undefined {
  const v = cost && typeof cost === 'object' ? (cost as { spent_usd?: unknown }).spent_usd : undefined;
  return typeof v === 'number' ? v : undefined;
}

/** One text frame, typed, or null (not JSON, or a type this client does not handle: ignored). */
export function parseNativeFrame(text: string): NativeFrame | null {
  let raw: unknown;
  try { raw = JSON.parse(text); } catch { return null; }
  if (!raw || typeof raw !== 'object') return null;
  const m = raw as Record<string, unknown>;
  switch (m.type) {
    case 'state':
      return { type: 'state', mode: typeof m.mode === 'string' ? m.mode : undefined,
        conversation_id: typeof m.conversation_id === 'string' ? m.conversation_id : undefined,
        principal: typeof m.principal === 'string' ? m.principal : undefined, caps: m.caps };
    case 'tools':
      return { type: 'tools',
        calls: Array.isArray(m.calls) ? m.calls.filter((c): c is NativeToolCall => !!c && typeof (c as { call_id?: unknown }).call_id === 'string') : [],
        refused: Array.isArray(m.refused) ? m.refused : [], claims_blocked: Array.isArray(m.claims_blocked) ? m.claims_blocked : [] };
    case 'transcript':
      if (m.who !== 'user' && m.who !== 'arturo') return null;
      return { type: 'transcript', who: m.who, text: typeof m.text === 'string' ? m.text : '', new: m.new === true,
        ...(m.withdrawn === true ? { withdrawn: true } : {}) };
    case 'audio_end': return { type: 'audio_end' };
    case 'barge': return { type: 'barge', cut: m.cut === true };
    case 'tick': return { type: 'tick', elapsed_s: typeof m.elapsed_s === 'number' ? m.elapsed_s : undefined,
      spent_usd: spentUsd(m.cost) };
    case 'notice':
      return typeof m.text === 'string' && m.text.trim() ? { type: 'notice', text: m.text.trim(), ...(typeof m.code === 'string' ? { code: m.code } : {}) } : null;
    case 'refused':
      return { type: 'refused', reason: typeof m.reason === 'string' && m.reason.trim() ? m.reason.trim() : 'Arturo refused the call' };
    case 'stopped': return { type: 'stopped', reason: typeof m.reason === 'string' ? m.reason : undefined, report: m.report };
    default: return null;
  }
}

// ── the transcript rows ──────────────────────────────────────────────────────────────────────────────────────────

export interface TranscriptRow { who: 'user' | 'arturo'; text: string; ts: number; closed: boolean }
export interface TranscriptState { rows: TranscriptRow[]; open: { user: number | null; arturo: number | null } }
/** What the UI should do: a live caption changed (text '' clears it), or a row closed and becomes a turn. */
export type TranscriptEffect = { kind: 'partial'; who: 'user' | 'arturo'; text: string } | { kind: 'final'; who: 'user' | 'arturo'; text: string };

/** Only punctuation and spaces ("." / "..." / " , "): nothing a caption or a journal line should hold on its own. */
export function isPunctuationOnly(text: string): boolean {
  return /^[\s\p{P}\p{S}]*$/u.test(text);
}

export const emptyTranscript = (): TranscriptState => ({ rows: [], open: { user: null, arturo: null } });

function closeRow(s: TranscriptState, who: 'user' | 'arturo', fx: TranscriptEffect[]): TranscriptState {
  const at = s.open[who];
  if (at === null) return s;
  const rows = s.rows.slice();
  rows[at] = { ...rows[at], closed: true };
  if (rows[at].text.trim()) fx.push({ kind: 'final', who, text: rows[at].text });
  else fx.push({ kind: 'partial', who, text: '' });
  return { rows, open: { ...s.open, [who]: null } };
}

/**
 * Apply one frame. `text` is the WHOLE utterance so far: new:true starts a row for that speaker (closing its open
 * one), new:false replaces that speaker's open row (or, once closed, corrects its last row for the journal only),
 * withdrawn removes Arturo's open row. A speaker's turn closes when the other speaker starts a new row ONLY for the
 * caller (Arturo answering means you finished), and on audio_end for Arturo, so turns commit in the order spoken.
 */
export function reduceTranscript(s: TranscriptState, f: NativeFrame, now: number): { state: TranscriptState; effects: TranscriptEffect[] } {
  const fx: TranscriptEffect[] = [];
  if (f.type === 'audio_end') return { state: closeRow(s, 'arturo', fx), effects: fx };
  if (f.type !== 'transcript') return { state: s, effects: fx };
  const who = f.who;
  if (f.withdrawn) {
    if (who !== 'arturo' || s.open.arturo === null) return { state: s, effects: fx };
    const at = s.open.arturo;
    const rows = s.rows.filter((_, i) => i !== at);
    const shift = (i: number | null) => (i !== null && i > at ? i - 1 : i);
    fx.push({ kind: 'partial', who: 'arturo', text: '' });
    return { state: { rows, open: { user: shift(s.open.user), arturo: null } }, effects: fx };
  }
  let st = s;
  if (f.new || st.open[who] === null) {
    if (!f.new) {
      // An update with no open row. It may correct this speaker's last (closed) row only when it EXTENDS it (a late
      // finish of the same words: the journal gets the longer text). Anything else is new speech: the native server
      // restarts Arturo's running text when the caller cuts in, so his next chunk can arrive new:false holding only
      // "." (e2e 2026-11-19: "Checking" was overwritten with "."). Pure punctuation is dropped; other text opens a row.
      const last = st.rows.map((r) => r.who).lastIndexOf(who);
      if (last >= 0) {
        const prev = st.rows[last].text;
        if (f.text.startsWith(prev)) {
          const rows = st.rows.slice();
          rows[last] = { ...rows[last], text: f.text };
          return { state: { ...st, rows }, effects: fx };
        }
        if (isPunctuationOnly(f.text)) return { state: st, effects: fx };
      }
    }
    st = closeRow(st, who, fx);
    if (who === 'arturo') st = closeRow(st, 'user', fx);
    const rows = [...st.rows, { who, text: f.text, ts: now, closed: false }];
    st = { rows, open: { ...st.open, [who]: rows.length - 1 } };
  } else {
    const at = st.open[who]!;
    const rows = st.rows.slice();
    rows[at] = { ...rows[at], text: f.text };
    st = { ...st, rows };
  }
  fx.push({ kind: 'partial', who, text: f.text });
  return { state: st, effects: fx };
}

/** Close every open row (the call ended): the last lines become turns too. */
export function closeAll(s: TranscriptState): { state: TranscriptState; effects: TranscriptEffect[] } {
  const fx: TranscriptEffect[] = [];
  return { state: closeRow(closeRow(s, 'user', fx), 'arturo', fx), effects: fx };
}

/** The journal's turns: every non-empty row, in the order spoken, ts in epoch seconds. */
export function journalTurns(s: TranscriptState): Array<{ role: 'user' | 'arturo'; text: string; ts: number }> {
  return s.rows.filter((r) => r.text.trim()).map((r) => ({ role: r.who, text: r.text, ts: r.ts / 1000 }));
}

// ── tools ────────────────────────────────────────────────────────────────────────────────────────────────────────

/** A short one-line summary of a tool's args for the call card. */
export function argsSummary(args: unknown, max = 80): string | undefined {
  if (args === undefined || args === null) return undefined;
  let s: string;
  try { s = typeof args === 'string' ? args : JSON.stringify(args); } catch { return undefined; }
  if (!s || s === '{}') return undefined;
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** The snapshot is cumulative: the calls not seen before, in order. `seen` is updated in place. */
export function newToolCalls(seen: Set<string>, f: Extract<NativeFrame, { type: 'tools' }>): NativeToolCall[] {
  const out: NativeToolCall[] = [];
  for (const c of f.calls) {
    if (seen.has(c.call_id)) continue;
    seen.add(c.call_id);
    out.push(c);
  }
  return out;
}

// ── engine choice + wording ──────────────────────────────────────────────────────────────────────────────────────

export type VoiceEngine = 'native' | 'relay';

/** Native only when the server says it is ready, and only for Arturo's own call (an agent's call is relay-only). */
export function chooseEngine(ready: { ok: boolean } | null, agentId?: string | null): VoiceEngine {
  return !agentId && ready?.ok === true ? 'native' : 'relay';
}

/** GET /api/voice/native/ready -> {ok, reason?}, or null when it gives no usable answer (old server, timeout). */
export async function fetchNativeReady(fetchFn: typeof fetch = fetch, timeoutMs = 3000): Promise<{ ok: boolean; reason?: string } | null> {
  try {
    const r = await fetchFn('/api/voice/native/ready', { signal: AbortSignal.timeout(timeoutMs) });
    if (!r.ok) return null;
    const d = await r.json().catch(() => null) as { ok?: unknown; reason?: unknown } | null;
    if (!d || typeof d.ok !== 'boolean') return null;
    return d.ok ? { ok: true } : { ok: false, ...(typeof d.reason === 'string' ? { reason: d.reason } : {}) };
  } catch { return null; }
}

/** A refusal before the call went live that the relay engine can cover (native down or switched off). */
export function shouldFallBack(code: number, wentLive: boolean): boolean {
  return !wentLive && (code === 4502 || code === 4503);
}

/** What to tell the caller when the socket closed with `code`, or null for an ordinary end. */
export function closeMessage(code: number, reason: string, hungUp: boolean): string | null {
  if (hungUp) return null;
  const r = (reason || '').trim();
  if (code === 1000) return r === 'idle' ? 'the call ended after two minutes with no audio' : null;
  if (code === 1001) return 'the voice gateway restarted, so the call ended';
  if (code === 1011) return 'the voice service dropped the call';
  if (code === 1006 || code === 1005) return 'the connection to the voice service was lost';
  if (code >= 4000 && code <= 4999) {
    if (code === 4401 || code === 4403) return 'the voice gateway refused this server';
    return r || `the voice gateway refused the call (HTTP ${code - 4000})`;
  }
  return r || `the call ended (code ${code})`;
}
