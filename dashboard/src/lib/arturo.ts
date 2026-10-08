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
export interface ArturoHealth { live?: boolean; onboarded?: boolean; operator?: OperatorFacts; ok: boolean; status?: number; brain?: ArturoBrain; brain_mode?: string; mode?: 'voice' | 'text-only'; voice?: boolean; stt?: ArturoStt; error?: string }
/** A tap-to-pick card the BRAIN wrote with ask_choices: its options, and whether several may be picked.
 *  `note` is the server's own line (the starter team's cost), never the model's. */
export type ChoiceCard = { options: string[]; multi: boolean; purpose: 'starter_team' | 'devices' | 'other'; note?: string; exclusive?: string };
/** A pairing code card (pair_device). The brain never sees `code`; it is shown here and nowhere else. */
export type PairCard = { device: string; device_id: string; code: string; expires_in_s: number; where: string; powers: string; revoke: string;
  /** Page-only: set when a later turn's check_paired saw this device connect. The code is then dropped. */
  paired?: boolean };

export interface ArturoReply { choices?: ChoiceCard; pair_card?: PairCard; paired?: string[]; onboarding?: { done: boolean }; operator?: OperatorFacts; ok: boolean; status?: number; reply_text?: string; conversation_id?: string; brain?: ArturoBrain; tools_called?: string[]; spawned?: string[]; error?: string; detail?: unknown; provider?: string; model?: string; reason?: string; field?: string }
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

/** Where the first thread starts: the runtime check (a brain must exist before it is asked to
 *  listen), then the onboarding the brain runs from its instructions; an onboarded browser goes
 *  straight to the thread. */
export function firstStep(onboarded: boolean): 'runtime' | 'done' {
  return onboarded ? 'done' : 'runtime';
}

/** The page's invisible opener: it lets the brain speak first. Never shown as the operator's words
 *  (services/arturo/onboarding.py OPENER_SENTINEL). */
export const ONBOARDING_OPENER = '(first run: the operator just opened OrchestraOS)';
const LEGACY_OPENERS = ['Introduce my team.', 'Explain how seats are organised here.'];
/** True for a stored user turn that the PAGE sent, not the operator: the opener, and the two openers
 *  older pages sent. Hidden in the thread, including when a thread is reloaded. */
export function isPageOpener(text: string): boolean {
  const t = (text || '').trim();
  return t.startsWith('(first run:') || LEGACY_OPENERS.includes(t);
}

/** One tap on a multi-select card: toggles `option`, keeps the card's order, and keeps an exclusive
 *  option exclusive. */
export function toggleChoice(options: string[], picked: string[], option: string, exclusive?: string): string[] {
  if (exclusive && option === exclusive) return picked.includes(option) ? [] : [option];
  const next = new Set(picked.filter((p) => p !== exclusive));
  if (next.has(option)) next.delete(option); else next.add(option);
  return options.filter((o) => next.has(o));
}

/** What the voice controls are called (the operator, 2026-10-08: say what each one is, and never ask
 *  them to approve what they already have). Dictation is theirs as soon as the browser grants the mic;
 *  OrchestraOS asks nothing. Live voice mode (Arturo talks back, live) is the one thing that
 *  needs the server's voice key. The old one-word label named neither, so it is gone. */
export const DICTATE_TITLE = "Dictate (uses your browser's mic permission)";
/** A working name (the operator may rename it): change it here and every label follows. */
export const HANDS_FREE = 'Live voice mode';
export function handsFreeTitle(keyPresent: boolean): string {
  return keyPresent
    ? `${HANDS_FREE}: talk, and Arturo talks back (uses GEMINI_API_KEY on your server)`
    : `${HANDS_FREE} needs a voice key: GEMINI_API_KEY on your server`;
}
/** The browser's call runs on Gemini Live, so it needs GEMINI_API_KEY (`/health.live`). A server from
 *  before that field said only whether ANY voice key was set; fall back to that. */
export function handsFreeReady(h: Pick<ArturoHealth, 'live' | 'voice'> | null | undefined): boolean {
  if (!h) return false;
  return typeof h.live === 'boolean' ? h.live : !!h.voice;
}

/** Onboarding ends by EFFECT: only when the server's reply says so (finish_onboarding wrote its flag),
 *  never on a failed turn and never on the reply's wording. */
export function onboardingDone(r: Pick<ArturoReply, 'ok' | 'onboarding'> | null | undefined): boolean {
  return !!r && r.ok !== false && r.onboarding?.done === true;
}

/** The onboarding turn a surface sends: a first-line marker the proxy strips and turns into the
 *  playbook (services/arturo/onboarding.py). No parsing happens on this side, ever. */
export function onboardingTurn(step: 'onboarding' | 'onboarding_open', text: string): string {
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
