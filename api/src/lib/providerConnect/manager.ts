/**
 * ConnectManager — one install + sign-in attempt per provider, owned by the server.
 *
 *   preflight → installing → verifying → starting → waiting (→ checking_code, Gemini) → signed_in
 *   error (with a code) and expired (renewals used up) end an attempt; cancel forgets it.
 *
 * The person never sees a terminal: the server runs each step, reads the CLI's screen (parsers.ts) and hands the
 * sheet a link, a code and a deadline. Rules:
 *   - one attempt per provider: starting again while one is live returns the same attempt (same code, same timer);
 *   - nothing stale is ever served: a `login-<cli>` session this manager did not start (the old terminal flow, a
 *     previous API process) is killed before a sign-in starts, and an expired code is replaced before it is shown;
 *   - an expired code / a CLI that ended without signing in is renewed by itself, at most `maxRenewals` times;
 *   - an attempt nobody is watching is cancelled after `abandonMs`;
 *   - ONE install per provider at a time: a Try again, a second tab or a new attempt awaits the install already running
 *     instead of starting another npm / curl into ~/.local. An install is never cut short by Cancel or cleanup (that is
 *     what leaves half an install); Cancel is refused while one runs, and an attempt dropped meanwhile simply does not
 *     go on to sign in. Only the install's own timeout kills it (its whole process group, productionDeps.ts);
 *   - the sessions this flow starts are marked (tmux user option), so a restart sweeps only its own leftovers.
 *
 * Every side effect goes through ConnectDeps, so the tests drive it with a fake tmux, exec and clock.
 */
import { SPECS, type ConnectProviderId, type ProviderSpec } from './specs.js';
import { exitCode, parseAgyPane, parseCodexPane, parseGrokPane, validAgyCode, npmPermissionError, agyCodeRejected } from './parsers.js';

export type Phase = 'preflight' | 'installing' | 'verifying' | 'starting' | 'waiting' | 'checking_code'
  | 'signed_in' | 'error' | 'expired';

export type ErrorCode = 'preflight_tools' | 'preflight_bin_dir' | 'preflight_disk' | 'preflight_network'
  | 'install_failed' | 'install_permission' | 'install_not_visible' | 'start_failed' | 'device_auth_disabled'
  | 'cli_exited';

export type PreflightIssue = { code: 'preflight_tools'; missing: string[] } | { code: 'preflight_bin_dir' }
  | { code: 'preflight_disk'; freeBytes: number } | { code: 'preflight_network'; host: string };

export interface TmuxDeps {
  has(session: string): Promise<boolean>;
  /** New detached session running `script` in `cwd`. */
  start(session: string, cwd: string, script: string): Promise<boolean>;
  capture(session: string): Promise<string>;
  /** Literal text (tmux -l), never through a shell. */
  sendLiteral(session: string, text: string): Promise<void>;
  sendKey(session: string, key: 'Enter'): Promise<void>;
  kill(session: string): Promise<void>;
  /** Mark a session as started by this flow (a tmux user option). */
  mark(session: string): Promise<void>;
  /** Was this session started by this flow? */
  isOurs(session: string): Promise<boolean>;
}

export interface ConnectDeps {
  now(): number;
  tmux: TmuxDeps;
  /** Already signed in (codex login status / grok models / agy token file). */
  signedIn(spec: ProviderSpec): Promise<boolean>;
  /** The binary is on disk where the installers put it. */
  binInstalled(spec: ProviderSpec): boolean;
  /** The API's own PATH resolves it (what the runtime probe uses). */
  visible(spec: ProviderSpec): boolean;
  /** Tools, ~/.local/bin, and, when an install is due, disk and network. Null = fine. */
  preflight(spec: ProviderSpec, opts: { install: boolean }): Promise<PreflightIssue | null>;
  install(spec: ProviderSpec): Promise<{ ok: boolean; log: string }>;
  /** grok: link ~/.local/bin/grok to ~/.grok/bin/grok when the installer did not. */
  ensureLink(spec: ProviderSpec): void;
  version(spec: ProviderSpec): Promise<string | null>;
  /** The empty directory a sign-in runs in. */
  signinCwd(spec: ProviderSpec): string;
  signinScript(spec: ProviderSpec): string;
  /** The runtime catalog must be probed again (installed / signed in changed). */
  refreshRuntimes(): void;
}

export interface ConnectOptions {
  startTimeoutMs: number;
  checkEveryMs: number;
  codeCheckMs: number;
  abandonMs: number;
  hardMaxMs: number;
  keepEndedMs: number;
  maxRenewals: number;
}

export const DEFAULT_OPTIONS: ConnectOptions = {
  startTimeoutMs: 45_000,
  checkEveryMs: 3_000,
  codeCheckMs: 30_000,
  abandonMs: 10 * 60_000,
  hardMaxMs: 70 * 60_000,   // four 15-minute codex codes (the first + 3 renewals) and some slack
  keepEndedMs: 10 * 60_000,
  maxRenewals: 3,
};

/** What the sheet is sent. */
export interface ConnectView {
  provider: ConnectProviderId;
  phase: Phase;
  /** needed: the CLI was missing and is (being) installed; skipped: it was already there; null: not checked yet. */
  install: 'needed' | 'skipped' | null;
  url: string | null;
  code: string | null;
  expiresAt: number | null;
  /** expiresAt minus the server's now: the client anchors it to its own clock (the two clocks may differ). */
  expiresInMs: number | null;
  needsCode: boolean;
  /** 'renewed': the last code expired and this is a new one. 'code_renewed': a refused code ended the CLI, new link. */
  notice: 'renewed' | 'code_renewed' | null;
  codeError: 'code_rejected' | 'code_unconfirmed' | null;
  error: { code: ErrorCode; detail?: string } | null;
  renewals: number;
  maxRenewals: number;
  version: string | null;
  attemptId: number;
}

interface Attempt {
  id: number;
  spec: ProviderSpec;
  phase: Phase;
  install: 'needed' | 'skipped' | null;
  url: string | null;
  code: string | null;
  expiresAt: number | null;
  notice: ConnectView['notice'];
  codeError: ConnectView['codeError'];
  error: ConnectView['error'];
  renewals: number;
  version: string | null;
  startedAt: number;
  seenAt: number;
  endedAt: number | null;
  sessionAt: number;
  lastCheckAt: number;
  menuSent: boolean;
  codeSentAt: number;
  /** The screen when the code was sent, and the code: a refusal is judged on lines that are new since then. */
  codeBefore: string;
  codeSent: string;
  /** The install log, then the last pane: what Details shows. */
  output: string;
  /** A step is running (setup chain or a poll): tick skips this attempt. */
  busy: boolean;
}

const ENDED: Phase[] = ['signed_in', 'error', 'expired'];
const INSTALL_PHASES: Phase[] = ['preflight', 'installing', 'verifying'];
const tail = (s: string, n = 4000) => (s.length > n ? s.slice(-n) : s);

export class ConnectManager {
  private attempts = new Map<ConnectProviderId, Attempt>();
  /** The install running per provider, shared by every attempt (one manager per API process). */
  private installs = new Map<ConnectProviderId, Promise<{ ok: boolean; log: string }>>();
  private jobs = new Set<Promise<void>>();
  private seq = 0;
  private ticking = false;
  readonly opts: ConnectOptions;

  constructor(private deps: ConnectDeps, opts: Partial<ConnectOptions> = {}) {
    this.opts = { ...DEFAULT_OPTIONS, ...opts };
  }

  /** Start a sign-in, or return the live one. `fresh` replaces whatever is there (Try again / Get a new code). */
  start(id: ConnectProviderId, opts: { fresh?: boolean } = {}): ConnectView {
    const cur = this.attempts.get(id);
    if (cur && !opts.fresh && !ENDED.includes(cur.phase)) {
      cur.seenAt = this.deps.now();
      return this.viewOf(cur);
    }
    const now = this.deps.now();
    const a: Attempt = {
      id: ++this.seq, spec: SPECS[id], phase: 'preflight', install: null, url: null, code: null, expiresAt: null,
      notice: null, codeError: null, error: null, renewals: 0, version: null, startedAt: now, seenAt: now,
      endedAt: null, sessionAt: 0, lastCheckAt: 0, menuSent: false, codeSentAt: 0, codeBefore: '', codeSent: '', output: '',
      busy: true,
    };
    this.attempts.set(id, a);
    this.track(this.setup(a));
    return this.viewOf(a);
  }

  /** The attempt as the sheet sees it; marks it watched. Null when there is none. */
  view(id: ConnectProviderId): ConnectView | null {
    const a = this.attempts.get(id);
    if (!a) return null;
    a.seenAt = this.deps.now();
    return this.viewOf(a);
  }

  /** Gemini: the code Google showed, typed into agy literally, then Enter. */
  async submitCode(id: ConnectProviderId, raw: unknown): Promise<{ ok: true; view: ConnectView } | { ok: false; reason: 'bad_code' | 'not_waiting' }> {
    const a = this.attempts.get(id);
    if (!a || a.spec.kind !== 'paste' || a.phase !== 'waiting' || a.busy) return { ok: false, reason: 'not_waiting' };
    const code = validAgyCode(raw);
    if (!code) return { ok: false, reason: 'bad_code' };
    a.busy = true;
    try {
      a.codeBefore = await this.deps.tmux.capture(a.spec.session);
      a.codeSent = code;
      await this.deps.tmux.sendLiteral(a.spec.session, code);
      await this.deps.tmux.sendKey(a.spec.session, 'Enter');
      a.phase = 'checking_code';
      a.codeSentAt = this.deps.now();
      a.codeError = null;
      a.notice = null;
      a.seenAt = a.codeSentAt;
    } finally { a.busy = false; }
    return { ok: true, view: this.viewOf(a) };
  }

  /** Stop and forget: the session is killed. Refused while the CLI is being checked / installed / verified, so an
   *  install is never left half done. */
  async cancel(id: ConnectProviderId): Promise<'cancelled' | 'none' | 'installing'> {
    const a = this.attempts.get(id);
    if (!a) return 'none';
    if (INSTALL_PHASES.includes(a.phase)) return 'installing';
    this.attempts.delete(id);
    await this.killQuietly(a.spec.session);
    return 'cancelled';
  }

  /** At API start: kill the `login-<cli>` sessions this flow marked as its own that hold no attempt (a previous
   *  process's). Unmarked sessions (the old terminal flow, anything typed by hand) are left alone. */
  async sweepOrphans(): Promise<string[]> {
    const killed: string[] = [];
    for (const spec of Object.values(SPECS)) {
      if (this.attempts.has(spec.id)) continue;
      try {
        if (await this.deps.tmux.has(spec.session) && await this.deps.tmux.isOurs(spec.session)) {
          await this.deps.tmux.kill(spec.session);
          killed.push(spec.session);
        }
      } catch { /* tmux unavailable: nothing to sweep */ }
    }
    return killed;
  }

  /** True while an install for this provider is running. */
  installing(id: ConnectProviderId): boolean { return this.installs.has(id); }

  /** For Details: the live screen while the CLI runs, else what was kept (install log or last screen). */
  async output(id: ConnectProviderId): Promise<string> {
    const a = this.attempts.get(id);
    if (!a) return '';
    if (a.sessionAt && !ENDED.includes(a.phase) && await this.deps.tmux.has(a.spec.session)) {
      return tail(await this.deps.tmux.capture(a.spec.session));
    }
    return a.output;
  }

  /** True while any attempt is kept: the production loop has nothing to do otherwise. */
  get active(): boolean { return this.attempts.size > 0; }

  /** Advance every attempt one step. Never overlaps itself. */
  async tick(): Promise<void> {
    if (this.ticking) return;
    this.ticking = true;
    try {
      const now = this.deps.now();
      for (const [id, a] of [...this.attempts]) {
        if (ENDED.includes(a.phase)) {
          if (a.endedAt !== null && now - a.endedAt > this.opts.keepEndedMs) this.attempts.delete(id);
          continue;
        }
        if (now - a.seenAt > this.opts.abandonMs || now - a.startedAt > this.opts.hardMaxMs) {
          // Dropped. A running install is NOT stopped (it finishes, or its own timeout kills it); the dropped attempt
          // just never goes on to sign in.
          this.attempts.delete(id);
          if (!INSTALL_PHASES.includes(a.phase)) await this.killQuietly(a.spec.session);
          continue;
        }
        if (a.busy) continue;
        a.busy = true;
        try { await this.poll(a); } catch (err) {
          console.warn(`[provider-connect] poll ${id} failed: ${String(err)}`);
        } finally { a.busy = false; }
      }
    } finally { this.ticking = false; }
  }

  /** Tests: wait for every background step started so far. */
  async settle(): Promise<void> {
    while (this.jobs.size) await Promise.all([...this.jobs]);
  }

  // --- internals --------------------------------------------------------------------------

  private track(p: Promise<void>) {
    const job = p.catch((err) => { console.warn(`[provider-connect] ${String(err)}`); }).finally(() => { this.jobs.delete(job); });
    this.jobs.add(job);
  }

  private stale(a: Attempt): boolean { return this.attempts.get(a.spec.id) !== a; }

  private viewOf(a: Attempt): ConnectView {
    return {
      provider: a.spec.id, phase: a.phase, install: a.install, url: a.url, code: a.code, expiresAt: a.expiresAt,
      expiresInMs: a.expiresAt === null ? null : Math.max(0, a.expiresAt - this.deps.now()),
      needsCode: a.spec.kind === 'paste' && (a.phase === 'waiting' || a.phase === 'checking_code'),
      notice: a.notice, codeError: a.codeError, error: a.error, renewals: a.renewals,
      maxRenewals: this.opts.maxRenewals, version: a.version, attemptId: a.id,
    };
  }

  private end(a: Attempt, phase: Phase) {
    a.phase = phase;
    a.endedAt = this.deps.now();
  }

  private async fail(a: Attempt, code: ErrorCode, detail?: string) {
    a.error = detail ? { code, detail } : { code };
    if (a.sessionAt) {
      try { a.output = tail(await this.deps.tmux.capture(a.spec.session)) || a.output; } catch { /* keep what we have */ }
      await this.killQuietly(a.spec.session);
    }
    this.end(a, 'error');
  }

  private async killQuietly(session: string) {
    try { if (await this.deps.tmux.has(session)) await this.deps.tmux.kill(session); } catch { /* already gone */ }
  }

  private async succeed(a: Attempt) {
    await this.killQuietly(a.spec.session);
    try { this.deps.refreshRuntimes(); } catch { /* the picker re-probes on its own */ }
    a.url = null; a.code = null; a.expiresAt = null; a.notice = null; a.codeError = null;
    this.end(a, 'signed_in');
  }

  /** Preflight → install → verify → start the sign-in. Runs in the background; `busy` until the sign-in is up. */
  private async setup(a: Attempt): Promise<void> {
    const { spec } = a;
    try {
      if (await this.deps.signedIn(spec)) {
        if (this.stale(a)) return;
        a.install = 'skipped';
        await this.succeed(a);
        return;
      }
      if (this.stale(a)) return;
      // An install already running (another tab, the attempt this one replaced) is joined, never repeated.
      const missing = this.installs.has(spec.id) || !this.deps.binInstalled(spec);
      a.install = missing ? 'needed' : 'skipped';
      const issue = await this.deps.preflight(spec, { install: missing });
      if (this.stale(a)) return;
      if (issue) {
        const detail = issue.code === 'preflight_tools' ? issue.missing.join(', ')
          : issue.code === 'preflight_network' ? issue.host : undefined;
        await this.fail(a, issue.code, detail);
        return;
      }
      if (missing) {
        a.phase = 'installing';
        const r = await this.installOnce(spec);
        if (this.stale(a)) return;
        a.output = tail(r.log);
        if (!r.ok) { await this.fail(a, npmPermissionError(r.log) ? 'install_permission' : 'install_failed'); return; }
        try { this.deps.ensureLink(spec); } catch { /* verify below says whether it is reachable */ }
        a.phase = 'verifying';
        const v = await this.deps.version(spec);
        if (this.stale(a)) return;
        if (!v) { await this.fail(a, 'install_failed'); return; }
        a.version = v;
        if (!this.deps.visible(spec)) { await this.fail(a, 'install_not_visible'); return; }
        try { this.deps.refreshRuntimes(); } catch { /* best effort */ }
      } else {
        // Already on disk: the same checks as after an install. Grok in ~/.grok/bin but not linked gets its link.
        try { this.deps.ensureLink(spec); } catch { /* visible() below says whether it worked */ }
        a.version = await this.deps.version(spec);
        if (this.stale(a)) return;
        if (!this.deps.visible(spec)) { await this.fail(a, 'install_not_visible'); return; }
      }
      await this.startSignin(a);
    } finally { a.busy = false; }
  }

  /** The provider's running install, or a new one. Removed from the map when it ends, however it ends. */
  private installOnce(spec: ProviderSpec): Promise<{ ok: boolean; log: string }> {
    const running = this.installs.get(spec.id);
    if (running) return running;
    const p = this.deps.install(spec)
      .catch((err) => ({ ok: false, log: String(err) }))
      .finally(() => { if (this.installs.get(spec.id) === p) this.installs.delete(spec.id); });
    this.installs.set(spec.id, p);
    return p;
  }

  /** Replace whatever `login-<cli>` session exists (never show a leftover) and start the sign-in in it. */
  private async startSignin(a: Attempt): Promise<void> {
    const { spec } = a;
    a.phase = 'starting';
    a.url = null; a.code = null; a.expiresAt = null; a.menuSent = false; a.codeBefore = ''; a.codeSent = '';
    await this.killQuietly(spec.session);
    if (this.stale(a)) return;
    const ok = await this.deps.tmux.start(spec.session, this.deps.signinCwd(spec), this.deps.signinScript(spec));
    if (this.stale(a)) { await this.killQuietly(spec.session); return; }
    a.sessionAt = this.deps.now();
    a.lastCheckAt = a.sessionAt;
    if (!ok) { await this.fail(a, 'start_failed'); return; }
    // Ours: a later API process may sweep it (sweepOrphans); nobody else's session ever carries this mark.
    try { await this.deps.tmux.mark(spec.session); } catch { /* unmarked: never swept, still killed on the next start */ }
  }

  private async renew(a: Attempt, notice: 'renewed' | 'code_renewed') {
    if (a.renewals >= this.opts.maxRenewals) {
      try { a.output = tail(await this.deps.tmux.capture(a.spec.session)) || a.output; } catch { /* keep */ }
      await this.killQuietly(a.spec.session);
      a.url = null; a.code = null; a.expiresAt = null;
      this.end(a, 'expired');
      return;
    }
    a.renewals += 1;
    a.notice = notice;
    await this.startSignin(a);
  }

  /** One look at a running sign-in. */
  private async poll(a: Attempt): Promise<void> {
    const { spec } = a;
    const now = this.deps.now();
    const alive = await this.deps.tmux.has(spec.session);
    const pane = alive ? await this.deps.tmux.capture(spec.session) : '';
    const ex = alive ? exitCode(pane) : -1;

    // Signed in? On the CLI's own success, and every checkEveryMs while a code is out.
    const waiting = a.phase === 'waiting' || a.phase === 'checking_code';
    if (ex === 0 || (waiting && now - a.lastCheckAt >= this.opts.checkEveryMs)
        || (a.phase === 'checking_code')) {
      a.lastCheckAt = now;
      if (await this.deps.signedIn(spec)) { await this.succeed(a); return; }
    }

    if (spec.kind === 'device') {
      const s = spec.id === 'codex' ? parseCodexPane(pane) : parseGrokPane(pane);
      if (s.deviceAuthDisabled) { await this.fail(a, 'device_auth_disabled'); return; }
      if (ex !== null) {
        // Ended without signing in. After a code was out: it lapsed (or was declined), so a new one. Before: broken.
        if (a.phase === 'waiting' && ex !== 0) { await this.renew(a, 'renewed'); return; }
        await this.fail(a, 'cli_exited', ex >= 0 ? `exit ${ex}` : 'session ended');
        return;
      }
      if (a.phase === 'starting' && s.url && s.code) {
        a.phase = 'waiting';
        a.url = s.url;
        a.code = s.code;
        // The CLI states the lifetime when it starts: count from then, never from when we first read it.
        a.expiresAt = s.expiresInMin ? a.sessionAt + s.expiresInMin * 60_000 : null;
        return;
      }
      if (a.phase === 'waiting' && a.expiresAt !== null && now >= a.expiresAt) { await this.renew(a, 'renewed'); return; }
    } else {
      const scr = parseAgyPane(pane);
      if (ex !== null) {
        if (a.phase === 'checking_code') { a.codeError = 'code_rejected'; await this.renew(a, 'code_renewed'); return; }
        if (a.phase === 'waiting') { await this.renew(a, 'renewed'); return; }
        await this.fail(a, 'cli_exited', ex >= 0 ? `exit ${ex}` : 'session ended');
        return;
      }
      if (scr.kind === 'menu' && a.phase === 'starting') {
        // Google OAuth is item 1 and pre-selected: one Enter, once.
        if (scr.googleSelected && !a.menuSent) {
          a.menuSent = true;
          await this.deps.tmux.sendKey(spec.session, 'Enter');
        }
      } else if (scr.kind === 'url') {
        if (a.phase === 'starting') { a.phase = 'waiting'; a.url = scr.url; return; }
        a.url = scr.url;
        if (a.phase === 'checking_code') {
          if (agyCodeRejected(a.codeBefore, pane, a.codeSent)) { a.phase = 'waiting'; a.codeError = 'code_rejected'; return; }
          if (now - a.codeSentAt >= this.opts.codeCheckMs) { a.phase = 'waiting'; a.codeError = 'code_unconfirmed'; return; }
        }
      }
    }
    if (a.phase === 'starting' && now - a.sessionAt > this.opts.startTimeoutMs) {
      await this.fail(a, 'start_failed');
    }
  }
}
