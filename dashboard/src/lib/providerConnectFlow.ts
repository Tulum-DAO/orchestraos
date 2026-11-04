/**
 * providerConnectFlow.ts — what the Connect sheet shows for a server-run install + sign-in (api/src/routes/
 * provider-connect.ts). The server owns the attempt; this turns its state into the sheet's words: a short step list,
 * the code and its countdown, the link, the paste box (Gemini), a notice when a code was renewed, and errors in plain
 * words with Try again. Pure, so every state is tested without rendering.
 */

export type ConnectPhase = 'idle' | 'preflight' | 'installing' | 'verifying' | 'starting' | 'waiting' | 'checking_code'
  | 'signed_in' | 'error' | 'expired';

export interface ConnectState {
  provider: string;
  phase: ConnectPhase;
  install?: 'needed' | 'skipped' | null;
  url?: string | null;
  code?: string | null;
  expiresAt?: number | null;
  /** The server's expiresAt minus ITS now. The sheet re-anchors it to this browser's clock (anchorExpiry). */
  expiresInMs?: number | null;
  needsCode?: boolean;
  notice?: 'renewed' | 'code_renewed' | null;
  codeError?: 'code_rejected' | 'code_unconfirmed' | null;
  error?: { code: string; detail?: string } | null;
  renewals?: number;
  maxRenewals?: number;
  version?: string | null;
  attemptId?: number;
}

/** The providers the server installs and signs in by itself. Others (Claude) keep the terminal. */
const MANAGED = new Set(['codex', 'grok', 'gemini']);
export const isManagedProvider = (id: string): boolean => MANAGED.has(id);

const ACTIVE: ConnectPhase[] = ['preflight', 'installing', 'verifying', 'starting', 'waiting', 'checking_code'];
export const isActivePhase = (p: ConnectPhase): boolean => ACTIVE.includes(p);

/** Checking / installing / verifying: Cancel is not offered (the server refuses it) so an install never stops half way. */
const INSTALL_PHASES: ConnectPhase[] = ['preflight', 'installing', 'verifying'];

/** The state with expiresAt on THIS browser's clock: received-at + expiresInMs. The server's own clock may be off
 *  from the browser's by minutes, which would make a countdown from its absolute time wrong. */
export function anchorExpiry(s: ConnectState, receivedAt: number): ConnectState {
  const ms = s.expiresInMs;
  return { ...s, expiresAt: typeof ms === 'number' && ms >= 0 ? receivedAt + ms : null };
}

/** The picker's button for a provider that can't be used yet: what the sheet will actually do. The server installs
 *  only Codex, Grok and Gemini; for any other (Claude) the sheet opens a terminal, so it never promises an install. */
export function connectButtonLabel(id: string, label: string, installed: boolean): string {
  if (isManagedProvider(id)) return installed ? 'Sign in' : `Install ${label}`;
  return installed ? 'Sign in' : 'Set up in a terminal';
}

/** A code submit the server refused, in plain words (the typed code stays in the box). */
export function codeSubmitWords(reason: string): string {
  if (reason === 'bad_code') return "That doesn't look like the code Google shows. Copy it again and paste it here.";
  if (reason === 'not_waiting') return "The sign-in isn't ready for a code right now. Wait a moment, then press Submit again.";
  return "Couldn't send the code. Try again.";
}

/** Codex refuses device-code sign-in until the account allows it. */
export const CODEX_SECURITY_URL = 'https://chatgpt.com/#settings/Security';

export type StepState = 'done' | 'active' | 'todo' | 'failed';
export interface FlowStep { label: string; state: StepState }

export type SheetKind = 'member' | 'start' | 'working' | 'code' | 'done' | 'error' | 'expired';

export interface SheetView {
  kind: SheetKind;
  /** One line for what is happening now ("Installing Codex…"). */
  status: string | null;
  steps: FlowStep[];
  primary: { label: string; action: 'start' | 'retry' | 'new_code' } | null;
  prerequisite: { text: string; href: string; link: string } | null;
  url: string | null;
  code: string | null;
  expiresAt: number | null;
  pasteBox: boolean;
  notice: string | null;
  codeError: string | null;
  error: string | null;
  /** Cancel is offered while the server is working on it. */
  cancellable: boolean;
}

export interface ProviderInfo { id: string; label: string; installed: boolean }

const ORDER: ConnectPhase[] = ['preflight', 'installing', 'verifying', 'starting', 'waiting', 'checking_code', 'signed_in'];

/** The step list: Check · Install (only when it was needed) · Start sign-in · Sign in. */
export function flowSteps(s: ConnectState, label: string): FlowStep[] {
  const failedAt = s.phase === 'error' || s.phase === 'expired' ? s.phase : null;
  const at = ORDER.indexOf(s.phase);
  const installing = s.install === 'needed';
  const rows: { label: string; from: ConnectPhase; to: ConnectPhase }[] = [
    { label: 'Check this server', from: 'preflight', to: 'preflight' },
    ...(installing ? [{ label: `Install ${label}`, from: 'installing' as ConnectPhase, to: 'verifying' as ConnectPhase }] : []),
    { label: 'Start sign-in', from: 'starting', to: 'starting' },
    { label: 'Sign in', from: 'waiting', to: 'checking_code' },
  ];
  // An ended attempt marks the step it stopped on: the furthest one it had reached.
  const reached = failedAt ? failedStep(s) : at;
  return rows.map((r) => {
    const a = ORDER.indexOf(r.from); const b = ORDER.indexOf(r.to);
    if (reached > b) return { label: r.label, state: 'done' };
    if (reached >= a) return { label: r.label, state: failedAt ? 'failed' : 'active' };
    return { label: r.label, state: 'todo' };
  });
}

/** Where an ended attempt stopped, from its error code. */
function failedStep(s: ConnectState): number {
  if (s.phase === 'expired') return ORDER.indexOf('waiting');
  const code = s.error?.code ?? '';
  if (code.startsWith('preflight_')) return ORDER.indexOf('preflight');
  if (code.startsWith('install_')) return ORDER.indexOf('installing');
  if (code === 'start_failed' || code === 'device_auth_disabled') return ORDER.indexOf('starting');
  return ORDER.indexOf('waiting');
}

/** Errors in plain words. Never a code on its own. */
export function errorWords(e: { code: string; detail?: string } | null | undefined, label: string): string {
  switch (e?.code) {
    case 'preflight_tools': return `This server is missing a tool it needs${e.detail ? ` (${e.detail})` : ''}.`;
    case 'preflight_bin_dir': return "This server can't write to its ~/.local/bin folder, where the install goes.";
    case 'preflight_disk': return 'There is not enough free disk space on this server (it needs about 1 GB).';
    case 'preflight_network': return `This server can't reach ${e.detail || 'the download site'}. Check its internet connection.`;
    case 'install_failed': return `Installing ${label} didn't work.`;
    case 'install_permission': return `Installing ${label} didn't work: it tried to write to a folder this server's user can't change.`;
    case 'install_not_visible': return `${label} is installed, but OrchestraOS can't find it yet. Restart OrchestraOS, then try again.`;
    case 'start_failed': return `${label}'s sign-in didn't start.`;
    case 'device_auth_disabled': return 'Device code login is off for your ChatGPT account. Turn it on in ChatGPT → Settings → Security, then try again.';
    case 'cli_exited': return `${label} stopped unexpectedly.`;
    default: return 'Something went wrong.';
  }
}

const STATUS: Partial<Record<ConnectPhase, (label: string) => string>> = {
  preflight: () => 'Checking this server…',
  installing: (l) => `Installing ${l}…`,
  verifying: (l) => `Checking the ${l} install…`,
  starting: () => 'Starting sign-in…',
  waiting: () => 'Waiting for you to sign in…',
  checking_code: () => 'Checking the code…',
};

export function sheetView(s: ConnectState | null, p: ProviderInfo, admin: boolean): SheetView {
  const base: SheetView = {
    kind: 'start', status: null, steps: [], primary: null, prerequisite: null, url: null, code: null, expiresAt: null,
    pasteBox: false, notice: null, codeError: null, error: null, cancellable: false,
  };
  const prerequisite = p.id === 'codex'
    ? { text: 'First turn on device code login in ChatGPT → Settings → Security.', href: CODEX_SECURITY_URL, link: 'Open ChatGPT security settings' }
    : null;
  if (!admin) return { ...base, kind: 'member' };
  if (!s || s.phase === 'idle') {
    return { ...base, kind: 'start', prerequisite, primary: { label: p.installed ? `Sign in to ${p.label}` : `Install ${p.label}`, action: 'start' } };
  }
  const steps = flowSteps(s, p.label);
  if (s.phase === 'signed_in') return { ...base, kind: 'done', steps, status: `Signed in to ${p.label}` };
  if (s.phase === 'error') {
    return { ...base, kind: 'error', steps, error: errorWords(s.error, p.label), primary: { label: 'Try again', action: 'retry' },
      prerequisite: s.error?.code === 'device_auth_disabled' ? prerequisite : null };
  }
  if (s.phase === 'expired') {
    return { ...base, kind: 'expired', steps, error: 'The sign-in code expired.', primary: { label: 'Get a new code', action: 'new_code' } };
  }
  const renewing = s.phase === 'starting' && !!s.notice;
  const status = renewing ? (s.notice === 'code_renewed' ? 'Getting a new sign-in link…' : 'Getting a new code…') : STATUS[s.phase]?.(p.label) ?? null;
  const notice = s.notice === 'renewed' ? "That code expired — here's a new one."
    : s.notice === 'code_renewed' ? "That code didn't work — here's a new sign-in link." : null;
  const codeError = s.notice === 'code_renewed' ? null
    : s.codeError === 'code_rejected' ? "That code didn't work. Copy it again from the Google page and paste it here."
    : s.codeError === 'code_unconfirmed' ? "Couldn't confirm that code. Paste it again, or get a new one." : null;
  const showing = (s.phase === 'waiting' || s.phase === 'checking_code') && !!s.url;
  return {
    ...base,
    kind: showing ? 'code' : 'working',
    status,
    steps,
    prerequisite: showing || s.phase === 'starting' ? prerequisite : null,
    url: showing ? s.url ?? null : null,
    code: showing ? s.code ?? null : null,
    expiresAt: showing ? s.expiresAt ?? null : null,
    pasteBox: showing && !!s.needsCode,
    notice: showing || renewing ? notice : null,
    codeError: showing ? codeError : null,
    cancellable: !INSTALL_PHASES.includes(s.phase),
  };
}

/** "14:05" for what is left, "0:00" once it has passed; null with no deadline. */
export function countdown(expiresAt: number | null | undefined, now: number): string | null {
  if (!expiresAt) return null;
  const left = Math.max(0, Math.ceil((expiresAt - now) / 1000));
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
}

/** The code Google shows, checked before it is sent (the server checks again). */
export function looksLikeGoogleCode(raw: string): boolean {
  return /^[A-Za-z0-9/_.~-]{10,512}$/.test(raw.trim());
}

// --- the API --------------------------------------------------------------------------------

export type ConnectResult = { ok: true; state: ConnectState } | { ok: false; member?: boolean; error: string };

async function call(method: string, path: string, body?: unknown): Promise<ConnectResult> {
  try {
    const res = await fetch(`/api/providers/${path}`, {
      // Every POST / DELETE goes as JSON: the server refuses anything else (415).
      method, headers: method === 'GET' ? undefined : { 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
    });
    const json = await res.json().catch(() => ({}));
    if (res.status === 403) return { ok: false, member: true, error: 'not_admin' };
    if (!res.ok || !json.ok) return { ok: false, error: json.reason || json.error || `HTTP ${res.status}` };
    return { ok: true, state: json as ConnectState };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

export const connectApi = {
  get: (id: string) => call('GET', `${encodeURIComponent(id)}/connect`),
  start: (id: string, fresh = false) => call('POST', `${encodeURIComponent(id)}/connect`, { fresh }),
  code: (id: string, code: string) => call('POST', `${encodeURIComponent(id)}/connect/code`, { code: code.trim() }),
  cancel: (id: string) => call('DELETE', `${encodeURIComponent(id)}/connect`, {}),
  output: async (id: string): Promise<string> => {
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(id)}/connect/output`);
      const j = await r.json().catch(() => ({}));
      return typeof j?.output === 'string' ? j.output : '';
    } catch { return ''; }
  },
};
export type ConnectApi = typeof connectApi;

// --- keyboard and copy, kept pure so they can be tested without a DOM -------------------------

export interface SheetKey {
  key: string;
  shiftKey: boolean;
  /** The key was pressed inside the Claude terminal (xterm): it owns Esc and Tab there. */
  inTerminal: boolean;
  /** Focus is on the first / last focusable element of the sheet. */
  atFirst: boolean;
  atLast: boolean;
  /** How many focusable elements the sheet has. */
  count: number;
}

export interface SheetKeyAction {
  /** Keep the key from outer handlers (ArturoPanel's window Tab trap and Esc, which would pull focus under the sheet
   *  or close the panel behind it). */
  stop: boolean;
  /** Cancel the browser's own handling (only when focus is moved by hand). */
  prevent: boolean;
  action: 'none' | 'close' | 'focus-first' | 'focus-last' | 'focus-box';
}

/** What the sheet does with a key pressed inside it: Esc closes, Tab wraps, and nothing escapes to a trap outside.
 *  In the terminal, Esc and Tab belong to the terminal: left alone (still kept from outer handlers). */
export function sheetKeyAction(k: SheetKey): SheetKeyAction {
  if (k.key !== 'Escape' && k.key !== 'Tab') return { stop: false, prevent: false, action: 'none' };
  if (k.inTerminal) return { stop: true, prevent: false, action: 'none' };
  if (k.key === 'Escape') return { stop: true, prevent: true, action: 'close' };
  if (k.count === 0) return { stop: true, prevent: true, action: 'focus-box' };
  if (k.shiftKey && k.atFirst) return { stop: true, prevent: true, action: 'focus-last' };
  if (!k.shiftKey && k.atLast) return { stop: true, prevent: true, action: 'focus-first' };
  return { stop: true, prevent: false, action: 'none' };
}

interface CopyDoc {
  createElement(tag: 'textarea'): { value: string; setAttribute(n: string, v: string): void; style: Record<string, string>; select(): void; remove(): void };
  body: { appendChild(el: unknown): unknown };
  execCommand(cmd: string): boolean;
}

/** Copy without the async Clipboard API (missing on plain http over Tailscale): a hidden textarea +
 *  execCommand('copy'). Focus goes back to `restore` (the Copy button), never left outside the dialog. */
export function copyWithTextarea(doc: CopyDoc, text: string, restore: { focus(): void } | null): boolean {
  const ta = doc.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  Object.assign(ta.style, { position: 'fixed', opacity: '0', top: '0' });
  doc.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = doc.execCommand('copy'); } catch { ok = false; }
  ta.remove();
  try { restore?.focus(); } catch { /* the button may have gone */ }
  return ok;
}
