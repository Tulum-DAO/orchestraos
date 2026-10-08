/**
 * arturo.ts — the browser's client for Arturo's text path (tracks T2/T4).
 *
 * POST /api/arturo/text   -> gateway /arturo/text -> :5071/text (one tool-enabled turn on
 *                            whichever brain the install has: api key / authed CLI / none)
 * GET  /api/arturo/health -> brain {kind, runtime, model}, mode voice|text-only
 * GET  /api/runtimes/available -> the runtime catalog probe (installed + authed per CLI)
 *
 * The context record rides in front of the text as one line so every page's pill can
 * hand Arturo "where the user is" without a second contract.
 */
export interface ArturoBrain { kind: 'api' | 'runtime' | 'none'; runtime?: string; cli?: string; model: string; reason?: string; provider?: string }
export interface ArturoStt { server: boolean; backend: 'local-whisper' | 'none'; state: 'ready' | 'warming' | 'not-installed' | 'off' | 'error'; reason?: string; install?: string; model?: string }
export interface ArturoHealth { operator?: OperatorFacts; ok: boolean; status?: number; brain?: ArturoBrain; brain_mode?: string; mode?: 'voice' | 'text-only'; voice?: boolean; stt?: ArturoStt; error?: string }
/** The starter team as the server sees it after a 'team' onboarding turn (services/arturo
 *  starter_team_state). Only `state` is read here; the seats are for the brain. */
export type TeamState = { state: 'absent' | 'starting' | 'incomplete' | 'present' | 'other_manager' | 'unknown'; declined?: boolean; seats?: { name: string; tier: string; seen: string }[]; project?: string; manager?: string };

export interface ArturoReply { team?: TeamState; operator?: OperatorFacts; ok: boolean; status?: number; reply_text?: string; conversation_id?: string; brain?: ArturoBrain; tools_called?: string[]; spawned?: string[]; error?: string; detail?: unknown; provider?: string; model?: string; reason?: string; field?: string }
export interface ArturoContext { route: string; entityKind?: string; entityId?: string; hint?: string }
export interface RuntimeRow { id: string; label?: string; cli?: string; installed: boolean; authed: boolean | 'unverified'; auth_reason?: string | null }

export function contextLine(ctx?: ArturoContext | null): string {
  if (!ctx) return '';
  const bits = [`route=${ctx.route}`];
  if (ctx.entityKind) bits.push(`entity=${ctx.entityKind}${ctx.entityId ? ':' + ctx.entityId : ''}`);
  if (ctx.hint) bits.push(`hint=${ctx.hint}`);
  return `[Context: ${bits.join(' ')}]`;
}

/** Message states every Arturo chatmode shows under a sent turn (Shaw 2026-09-22, the Muse
 *  shape): sending = leaving the browser · sent = the server has the whole request · acked =
 *  Arturo answered it · failed = it did not get through (retry-able). */
export type SendState = 'sending' | 'sent' | 'acked' | 'failed';
export function sendStateLabel(state?: SendState | null): string {
  switch (state) {
    case 'sending': return 'Sending…';
    case 'sent': return 'Sent';
    case 'acked': return 'Acknowledged';
    case 'failed': return 'Not delivered';
    default: return '';
  }
}

export interface ArturoTextOptions {
  /** Fires the moment the request body has fully left the browser (XHR upload complete):
   *  the honest "Sent" edge, before the reply (which can take a minute) comes back. */
  onSent?: () => void;
  /** This turn's brain, when the operator chose one (null/undefined = the default brain). */
  brain?: { provider: string; model: string };
}

/** The /api/arturo/text body. Context is a FIELD now; the proxy renders the same line
 *  contextLine() produced, after the onboarding marker, so model input is unchanged.
 *  `brain` is sent only when the operator chose one (DEC-1790669162399904 §1.5-1.6). */
export function buildTextBody(text: string, conversationId: string, ctx?: ArturoContext | null,
                              brain?: { provider: string; model: string }): Record<string, unknown> {
  const body: Record<string, unknown> = { text, conversation_id: conversationId };
  if (ctx) body.context = ctx;
  if (brain) body.brain = brain;
  return body;
}

export async function arturoText(text: string, conversationId: string, ctx?: ArturoContext | null, opts: ArturoTextOptions = {}): Promise<ArturoReply> {
  const body = JSON.stringify(buildTextBody(text, conversationId, ctx, opts.brain));
  // XMLHttpRequest, not fetch: fetch has no "request body delivered" event, and the Sent state
  // must be real (the server has it), not a timer.
  return new Promise<ArturoReply>((resolve) => {
    let xhr: XMLHttpRequest;
    try { xhr = new XMLHttpRequest(); } catch { resolve({ ok: false, error: 'network' }); return; }
    xhr.open('POST', '/api/arturo/text');
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.upload.onload = () => { opts.onSent?.(); };
    xhr.onload = () => {
      let json: Partial<ArturoReply> & { error?: string; detail?: unknown } = {};
      try { json = JSON.parse(xhr.responseText || '{}'); } catch { /* bad json */ }
      if (xhr.status < 200 || xhr.status >= 300) resolve({ ok: false, status: xhr.status, error: json.error || `HTTP ${xhr.status}`, detail: json.detail });
      else resolve(json as ArturoReply);
    };
    xhr.onerror = () => resolve({ ok: false, error: 'network' });
    xhr.ontimeout = () => resolve({ ok: false, error: 'timeout' });
    // ontimeout above never fired, because no timeout was ever set: a connection that stalled
    // after the server had answered left the bubble waiting for as long as the tab stayed open
    // (seen live, 2026-09-30). 200s sits just past the server's own 195s ceiling, so it can
    // only trip on a turn that is already lost.
    xhr.timeout = 200000;
    xhr.send(body);
  });
}

export async function arturoHealth(): Promise<ArturoHealth> {
  try {
    const res = await fetch('/api/arturo/health');
    const json = await res.json().catch(() => ({}));
    return res.ok ? (json as ArturoHealth) : { ok: false, status: res.status, error: json.error || `HTTP ${res.status}` };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

/** fresh=true busts the API's 300s in-process cache (POST /refresh re-probes): the
 *  onboarding "Check again" tap after a CLI login must see the new auth state at once
 *  (G14: the cached GET kept saying "installed but not logged in" for up to 5 minutes). */
export async function runtimesAvailable(fresh = false): Promise<RuntimeRow[]> {
  try {
    const res = await fetch(fresh ? '/api/runtimes/available/refresh' : '/api/runtimes/available',
                            fresh ? { method: 'POST' } : undefined);
    if (!res.ok) return [];
    const json = await res.json();
    return (json.providers || []) as RuntimeRow[];
  } catch { return []; }
}

/** Header/chip label for the brain: "Claude (CLI)", "gemini-2.5-flash", or "no brain". */
export function brainLabel(b?: ArturoBrain | null): string {
  if (!b) return '…';
  if (b.kind === 'none') return 'no brain';
  if (b.kind === 'runtime') {
    const m = b.model && !b.model.endsWith('-cli-default') ? b.model : (b.runtime || 'cli');
    return prettyModel(m);
  }
  return prettyModel(b.model);
}

export function prettyModel(id: string): string {
  const m = id.toLowerCase();
  if (m.includes('fable')) return 'Fable ' + (m.match(/(\d)-?(\d)?/)?.slice(1).filter(Boolean).join('.') || '');
  if (m.includes('opus')) return 'Opus ' + (m.match(/opus-(\d)(?:-(\d))?/)?.slice(1).filter(Boolean).join('.') || '');
  if (m.includes('sonnet')) return 'Sonnet ' + (m.match(/sonnet-(\d)(?:-(\d))?/)?.slice(1).filter(Boolean).join('.') || '');
  if (m.includes('haiku')) return 'Haiku ' + (m.match(/haiku-(\d)-(\d)/)?.slice(1).join('.') || '');
  if (m.startsWith('claude')) return 'Claude';
  if (m.startsWith('gemini')) return m.replace('gemini-', 'Gemini ').replace(/-/g, ' ');
  if (m.startsWith('gpt')) return m.toUpperCase().replace(/-/g, ' ');
  if (m === 'codex') return 'Codex';
  return id;
}

/** Server-side operator facts (services/arturo/operator_store.py); carried on /health and every /text reply. */
export type OperatorFacts = { name?: string | null; timezone?: string | null; role?: string | null; pronouns?: string | null; devices?: string | null };

/** Where the first thread starts. runtime first — a brain must exist before it is asked to
 *  listen; an onboarded browser goes straight to the thread. The known-name case is honoured
 *  inside the runtime step (stepAfterRuntime), so this never depends on localStorage alone. */
export function firstStep(onboarded: boolean): 'runtime' | 'done' {
  return onboarded ? 'done' : 'runtime';
}

/** After a successful runtime probe: ask the name only if the SERVER does not know it. */
export function stepAfterRuntime(operatorName?: string | null, textOnly?: boolean): 'name' | 'voice' | 'team' {
  if (!operatorName) return 'name';
  return textOnly ? 'voice' : 'team';
}

/** The team step ends on what the SERVER says after the turn, never on the reply's wording: the team
 *  is running, there is nothing this step may create (another manager, or an unreadable registry), or
 *  the operator said no (decline_starter_team ran). absent / starting / incomplete keep the step open:
 *  a question is not a no, and a failed, partial or still-running start can be asked about again. */
export function teamStepDone(team?: TeamState): boolean {
  return !!team && (team.declined === true || team.state === 'present' || team.state === 'other_manager' || team.state === 'unknown');
}

/** The onboarding turn a surface sends: a first-line marker the proxy strips and turns into the
 *  step directive (services/arturo/onboarding.py). No parsing happens on this side, ever. */
/** The devices card (onboarding step 'devices'). The SAME list, in the same order, as
 *  services/arturo/onboarding.py DEVICES, whose per-device facts the brain answers from; a test there
 *  reads this line, so the two cannot drift. */
export const DEVICE_OPTIONS = ['iPhone', 'iPad', 'Apple Watch', 'Mac', 'Android phone', 'Just this computer'];
/** Picking it clears the others, and picking any other clears it. */
export const DEVICE_ONLY_HERE = 'Just this computer';

/** One tap on a multi-select card: toggles `option`, keeps the card's order, and keeps the exclusive
 *  option exclusive. */
export function toggleChoice(options: string[], picked: string[], option: string, exclusive?: string): string[] {
  if (exclusive && option === exclusive) return picked.includes(option) ? [] : [option];
  const next = new Set(picked.filter((p) => p !== exclusive));
  if (next.has(option)) next.delete(option); else next.add(option);
  return options.filter((o) => next.has(o));
}

/** The operator's answer as it is sent, and as it reads in their bubble. */
export function devicesAnswer(picked: string[]): string {
  return `My devices: ${picked.join(', ')}`;
}

export function onboardingTurn(step: 'name' | 'team' | 'team_open' | 'devices', text: string): string {
  return `[Onboarding: step=${step}]\n${text}`;
}

export function greeting(name?: string | null): string {
  const h = new Date().getHours();
  const part = h < 5 ? 'Night' : h < 12 ? 'Morning' : h < 18 ? 'Afternoon' : 'Evening';
  return name ? `${part}, ${name}` : `${part}`;
}

export function slugify(s: string): string {
  // seat name = the first three meaningful words, e.g. "write a limerick about tmux" -> write-limerick-tmux
  const stop = new Set(['a', 'an', 'the', 'to', 'and', 'of', 'for', 'in', 'on', 'it', 'about', 'with', 'my', 'me', 'please']);
  const words = s.toLowerCase().replace(/[^a-z0-9\s-]+/g, ' ').split(/\s+/).filter((w) => w && !stop.has(w)).slice(0, 3);
  return words.join('-').slice(0, 28) || 'first-agent';
}

export function newConversationId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

const KIND: Record<string, string> = {
  approvals: 'approvals', agents: 'agents', agent: 'agent', projects: 'projects', tasks: 'tasks',
  inbox: 'inbox', roadmaps: 'roadmap', clients: 'clients', people: 'people', activity: 'activity',
};

// --- focused entity -------------------------------------------------------------------------
// Chat mode and dev mode open an agent as a FULL-SCREEN OVERLAY without changing the URL, so
// the route alone says "agents" and Arturo cannot tell WHICH agent you are sitting in. The
// overlay publishes its agent here and the pill prefers it over the route.
let _focus: { kind: string; id: string; label?: string } | null = null;
const _subs = new Set<() => void>();

export function setArturoFocus(f: { kind: string; id: string; label?: string } | null) {
  const same = (!_focus && !f) || (_focus && f && _focus.kind === f.kind && _focus.id === f.id);
  if (same) return;
  _focus = f;
  _subs.forEach((fn) => { try { fn(); } catch { /* a bad subscriber must not break the others */ } });
}
export function getArturoFocus() { return _focus; }
export function subscribeArturoFocus(fn: () => void): () => void {
  _subs.add(fn);
  return () => { _subs.delete(fn); };
}

export function contextFromLocation(pathname: string, params: Record<string, string | undefined>, search: string): ArturoContext {
  const seg = pathname.split('/').filter(Boolean);
  const kind = KIND[seg[0] || ''] || (seg[0] || 'overview');
  const q = new URLSearchParams(search);
  const entityId = params.id || params.projectSlug || params.clientId || q.get('id') || q.get('focus') || seg[1] || undefined;
  return { route: pathname, entityKind: kind, entityId };
}


// --- G15: boot window --------------------------------------------------------------------
// While `orchestra up` is still bringing the stack up, every hop answers 502/503/504 with its
// own words (dashboard-proxy "upstream unreachable", API "gateway token unavailable" /
// "gateway unreachable", gateway "arturo unreachable") or the fetch itself fails. All of that
// is one STARTING state, not an error the user should read as an HTTP code.
const STARTING_RE = /^(HTTP 50[234]|arturo unreachable|gateway unreachable|gateway token unavailable|upstream unreachable|network|Failed to fetch|Load failed|timeout)/i;

export function isStarting(r: { ok: boolean; status?: number; error?: string } | null | undefined): boolean {
  if (!r || r.ok) return false;
  if (r.status !== undefined && r.status >= 502 && r.status <= 504) return true;
  return STARTING_RE.test(String(r.error || ''));
}

export const STARTING_TEXT = 'Still starting — I will retry in a few seconds.';

/** Poll /api/arturo/health with backoff (1,2,3,5,5… s) until it answers ok or maxMs elapses.
 *  Resolves the last health seen; check `.ok` on the result. */
export async function waitForArturo(opts: { maxMs?: number; onTick?: (h: ArturoHealth, attempt: number) => void } = {}): Promise<ArturoHealth> {
  const maxMs = opts.maxMs ?? 90_000;
  const delays = [1000, 2000, 3000, 5000];
  const t0 = Date.now();
  let attempt = 0;
  let last: ArturoHealth = { ok: false, error: 'network' };
  for (;;) {
    last = await arturoHealth();
    if (last.ok || !isStarting(last)) return last;
    attempt += 1;
    opts.onTick?.(last, attempt);
    if (Date.now() - t0 >= maxMs) return last;
    await new Promise((r) => setTimeout(r, delays[Math.min(attempt - 1, delays.length - 1)]));
  }
}
