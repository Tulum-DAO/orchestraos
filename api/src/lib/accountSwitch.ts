/**
 * "Use this account for all agents" (account switch Level 1): the Settings › Usage Claude cards' button.
 *
 * The move itself is agent-orchestra's scripts/account_switch.py (orchestra-builder owns it; never changed here):
 *   python3 account_switch.py --shared <account>
 * moves <account>'s login into the fleet's shared ~/.claude/.credentials.json under both of Claude Code's refresh
 * locks, parks the login it displaces in state/account-vault/<id>/, never copies one, and prints ONE JSON line:
 *   rc 0  {"status": "switched" | "noop", "from", "to", "followers", "fp_from", "fp_to", ...}
 *   rc 2  {"status": "refused", "reason", "detail"}
 * Claude Code 2.1.284 stats that file before every model call and adopts a replaced one, so every agent on
 * ~/.claude moves at its next call: no respawn, no restart (INSESSION-PROBE.txt, LIVE PROOF 19:16Z).
 *
 * Where each login is comes from the switch itself, never inferred here:
 *   python3 account_switch.py --holders   ->  {account: {where: shared|seat|vault|home|dead, seat, path, live_pids}}
 * This module adds only the registry's display names (state/claude_profiles.json), the ledger's "a switch stopped
 * half-way" flag (state/account_locations.json pending), the operator's terms acknowledgement
 * (state/account_terms_ack.json), and whether a home dir has a login file at all (a stat; no login is ever opened).
 * It runs the CLI and words its answer. Fingerprints, paths and the CLI's detail text are never passed on.
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync, chmodSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export const TERMS_LINE = "Agents may use this account when you choose it. Anthropic's terms on using several accounts "
  + 'are a grey area; this is your call.';
export const CODEX_TERMS_LINE = "Agents may use this account when you choose it. OpenAI's terms on using several accounts apply to you.";
export const LABEL_RE = /^[A-Za-z0-9._-]{1,64}$/;
export type Provider = 'claude' | 'codex';
export const PROVIDERS: readonly Provider[] = ['claude', 'codex'];
export const SWITCH_TIMEOUT_MS = 60_000;

export interface AccountCard {
  id: string;
  /** "bizypro": the registry's name, else the first word of the e-mail domain in its note, else its id. */
  name: string;
  /** The account's e-mail address from the registry note (admin-only route), so Shaw can tell which login it is. */
  email: string | null;
  /** Where its login is now: the fleet's ~/.claude, a parked vault slot, its own home dir, or one agent's dir. */
  where: 'shared' | 'vault' | 'home' | 'seat';
  /** All agents run on it (it is in ~/.claude). */
  agents: boolean;
  /** "Use this account for all agents" would be accepted now (the switch itself has the final word). */
  canUse: boolean;
  /** Why not, in words, when canUse is false and it is not the one in use. */
  whyNot: string | null;
}

/** The page's top line: "All agents: <email> · switched 3:23 PM ET by shaw (dashboard)". */
export interface CurrentLogin {
  account: string;
  name: string;
  email: string | null;
  /** When the fleet last switched to it (ISO), from logs/account-switch.jsonl; null = never switched here. */
  since: string | null;
  /** That time in Shaw's zone (state/shaw-location.json): "3:23 PM ET", or "Sat 3:23 PM ET" on another day. */
  since_text: string | null;
  /** Who pressed: the dashboard's signed-in operator, else "hand (account_switch.py)". */
  by: string | null;
}

export interface AccountsView {
  provider: 'claude';
  /** All agents' login now, for the top line (null when --holders did not answer). */
  current: CurrentLogin | null;
  /** {seat root: account} for an agent that holds a login in its own dir (Level 2; empty while every agent shares
   *  ~/.claude). An agent's header names its account only when it differs from the fleet's. */
  seats: Record<string, string>;
  /** The account whose login the fleet's ~/.claude holds now (from the ledger, else the registry). */
  shared: string | null;
  /** How many Claude processes run on ~/.claude (they all follow a switch at their next call). */
  followers: number;
  terms_ack: boolean;
  terms_text: string;
  accounts: AccountCard[];
}

interface Profile { id: string; config_dir: string; name?: unknown; note?: unknown; provider?: unknown }
interface LedgerRow { where?: unknown; seat?: unknown; path?: unknown; at?: unknown }

const expand = (p: string, home: string) => (p === '~' ? home : p.startsWith('~/') ? join(home, p.slice(2)) : p);
function real(p: string): string { try { return realpathSync(p); } catch { return p; } }
function readJson(p: string): any { try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return null; } }

export function enrolledClaude(dataDir: string): Profile[] {
  const reg = readJson(join(dataDir, 'state', 'claude_profiles.json'));
  const list = reg && Array.isArray(reg.profiles) ? reg.profiles : [];
  return list.filter((p: any) => p && typeof p === 'object' && p.enrolled === true
    && (p.provider ?? 'claude') === 'claude' && typeof p.id === 'string' && LABEL_RE.test(p.id)
    && typeof p.config_dir === 'string' && p.config_dir);
}

export function registryProvider(dataDir: string, id: string): string | null {
  const reg = readJson(join(dataDir, 'state', 'claude_profiles.json'));
  const p = reg && Array.isArray(reg.profiles) ? reg.profiles.find((x: any) => x && x.id === id) : null;
  return p ? String(p.provider ?? 'claude') : null;
}

export function readLedger(dataDir: string): { accounts: Record<string, LedgerRow>; pending: boolean } {
  const d = readJson(join(dataDir, 'state', 'account_locations.json'));
  const acc = d && typeof d.accounts === 'object' && d.accounts && !Array.isArray(d.accounts) ? d.accounts : {};
  return { accounts: acc, pending: !!(d && d.pending) };
}

export interface Holder { where?: unknown; seat?: unknown; path?: unknown; live_pids?: unknown }
export type Holders = Record<string, Holder>;

export function displayName(p: Profile): string {
  if (typeof p.name === 'string' && p.name.trim() && p.name.trim().length <= 40) return p.name.trim();
  const m = /[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+)\.[A-Za-z0-9.-]+/.exec(String(p.note ?? ''));
  if (m) return m[1].toLowerCase();                         // the domain's first word; the address is never sent
  return p.id === 'default' ? 'Main login' : p.id;
}

/** Each provider's terms are acknowledged on their own: Claude's in state/account_terms_ack.json (as always), Codex's
 *  in state/account_terms_ack_codex.json. */
const termsAckPath = (dataDir: string, provider: Provider) =>
  join(dataDir, 'state', provider === 'codex' ? 'account_terms_ack_codex.json' : 'account_terms_ack.json');
export const termsLine = (provider: Provider = 'claude') => (provider === 'codex' ? CODEX_TERMS_LINE : TERMS_LINE);

export function termsAcknowledged(dataDir: string, provider: Provider = 'claude'): boolean {
  const t = readJson(termsAckPath(dataDir, provider));
  return !!(t && t.acknowledged === true);
}

/** The first press: the operator read the terms line next to the button. Atomic, 0600. */
export function recordTermsAck(dataDir: string, by: string, now: Date = new Date(), provider: Provider = 'claude'): void {
  const p = termsAckPath(dataDir, provider);
  mkdirSync(dirname(p), { recursive: true });
  const tmp = `${p}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tmp, JSON.stringify({ acknowledged: true, at: now.toISOString(), by, text: termsLine(provider) }), { mode: 0o600 });
  chmodSync(tmp, 0o600);
  renameSync(tmp, p);
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function emailOf(p: Profile): string | null {
  const m = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+/.exec(String(p.note ?? ''));
  return m ? m[0] : null;
}

/** The latest completed fleet-wide switch to `account` in logs/account-switch.jsonl (ISO ts), or null. Rows without a
 *  provider are Claude's; Codex rows carry provider "codex". */
export function lastFleetSwitch(dataDir: string, account: string, provider: Provider = 'claude'): string | null {
  let raw = '';
  try { raw = readFileSync(join(dataDir, 'logs', 'account-switch.jsonl'), 'utf8'); } catch { return null; }
  let best: string | null = null;
  for (const line of raw.slice(-1_048_576).split('\n')) {
    try {
      const d = JSON.parse(line);
      // Claude's fleet switch writes seat "*shared*"; the Codex switch writes kind "shared" (codex_account_switch.py)
      const fleetWide = provider === 'codex' ? d?.kind === 'shared' : d?.seat === '*shared*';
      if (d && fleetWide && d.status === 'switched' && d.to === account && typeof d.ts === 'string'
        && (d.provider ?? 'claude') === provider) best = d.ts;
    } catch { /* not a row */ }
  }
  return best;
}

/** The dashboard remembers who pressed (the switch's own audit line has no name). Atomic, 0600. */
const switchByPath = (dataDir: string, provider: Provider) =>
  join(dataDir, 'state', provider === 'codex' ? 'account_switch_by_codex.json' : 'account_switch_by.json');

export function recordSwitchBy(dataDir: string, to: string, by: string, now: Date = new Date(), provider: Provider = 'claude'): void {
  const p = switchByPath(dataDir, provider);
  mkdirSync(dirname(p), { recursive: true });
  const tmp = `${p}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tmp, JSON.stringify({ to, at: now.toISOString(), by: by.slice(0, 80) }), { mode: 0o600 });
  chmodSync(tmp, 0o600);
  renameSync(tmp, p);
}

export function switchedBy(dataDir: string, to: string, since: string | null, provider: Provider = 'claude'): string | null {
  if (!since) return null;
  const r = readJson(switchByPath(dataDir, provider));
  const t = Date.parse(since);
  if (r && r.to === to && typeof r.by === 'string' && Math.abs(Date.parse(r.at) - t) <= 300_000) return r.by;
  return 'hand (account_switch.py)';
}

/** Shaw's zone: state/shaw-location.json {tz, label}; else Tulum (his home base). */
export function shawZone(dataDir: string): { tz: string; label: string } {
  const d = readJson(join(dataDir, 'state', 'shaw-location.json'));
  if (d && typeof d.tz === 'string' && d.tz) {
    try { new Intl.DateTimeFormat('en-US', { timeZone: d.tz }); return { tz: d.tz, label: typeof d.label === 'string' && d.label ? d.label : d.tz }; } catch { /* bad zone */ }
  }
  return { tz: 'America/Cancun', label: 'Tulum' };
}

export function timeText(iso: string, zone: { tz: string; label: string }, now: Date = new Date()): string | null {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return null;
  const day = (d: Date) => new Intl.DateTimeFormat('en-US', { timeZone: zone.tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
  const hm = new Intl.DateTimeFormat('en-US', { timeZone: zone.tz, hour: 'numeric', minute: '2-digit' }).format(t);
  const wd = day(t) === day(now) ? '' : `${new Intl.DateTimeFormat('en-US', { timeZone: zone.tz, weekday: 'short' }).format(t)} `;
  return `${wd}${hm} ${zone.label}`;
}

/** The cards' state from the switch's own --holders answer. null with fewer than two Claude accounts enrolled: the
 *  Usage page stays as it is. holders null (the CLI could not be asked): every button disabled, saying so. */
export function accountsView(opts: { dataDir: string; holders: Holders | null; home?: string; now?: Date }): AccountsView | null {
  const home = opts.home ?? homedir();
  const profiles = enrolledClaude(opts.dataDir);
  if (profiles.length < 2) return null;
  const led = readLedger(opts.dataDir);
  const H = opts.holders && typeof opts.holders === 'object' ? opts.holders : null;
  const h = (id: string): Holder | null => (H && H[id] && typeof H[id] === 'object' ? H[id] : null);
  const pids = (x: Holder | null): number[] => (Array.isArray(x?.live_pids) ? (x!.live_pids as unknown[]).filter((n): n is number => Number.isInteger(n)) : []);
  const shared = profiles.find((p) => h(p.id)?.where === 'shared')?.id ?? null;
  const accounts: AccountCard[] = profiles.map((p) => {
    const x = h(p.id);
    const card: AccountCard = { id: p.id, name: displayName(p), email: emailOf(p), where: 'home', agents: false, canUse: false, whyNot: null };
    if (!H) return { ...card, whyNot: 'Where each login is cannot be read right now (account_switch --holders did not answer).' };
    if (p.id === shared) return { ...card, where: 'shared', agents: true };
    if (led.pending) return { ...card, whyNot: 'An earlier switch stopped half-way. Nothing moves until someone checks the switch ledger.' };
    if (!x) return { ...card, whyNot: 'The switch does not know this account.' };
    if (x.where === 'seat') {
      return { ...card, where: 'seat', whyNot: `The agent ${typeof x.seat === 'string' ? x.seat : '?'} holds this login (one holder per account).` };
    }
    if (x.where === 'vault') return { ...card, where: 'vault', canUse: true };
    if (x.where === 'dead') return { ...card, whyNot: `Signed out: Claude signed this login out after a failed refresh. Sign in to it again in ${p.config_dir}, then press.` };
    if (x.where !== 'home') return { ...card, whyNot: 'The switch reports this login somewhere this page does not know.' };
    const users = pids(x).sort((m, n) => m - n);
    if (users.length) {
      return { ...card, whyNot: `It is signed in in another Claude session (${plural(users.length, 'process', 'processes')}: pid ${users.slice(0, 3).join(', ')}). Close that session, then press again. A login is never copied.` };
    }
    if (!existsSync(join(real(expand(p.config_dir, home)), '.credentials.json'))) return { ...card, whyNot: `No login is in ${p.config_dir}: sign in there first.` };
    return { ...card, canUse: true };
  });
  const followers = shared ? pids(h(shared)).length : 0;
  let current: CurrentLogin | null = null;
  const sp = profiles.find((p) => p.id === shared);
  if (sp) {
    const since = lastFleetSwitch(opts.dataDir, sp.id);
    current = { account: sp.id, name: displayName(sp), email: emailOf(sp), since,
      since_text: since ? timeText(since, shawZone(opts.dataDir), opts.now) : null, by: switchedBy(opts.dataDir, sp.id, since) };
  }
  const seats: Record<string, string> = {};
  for (const p of profiles) {
    const x = h(p.id);
    if (x?.where === 'seat' && typeof x.seat === 'string' && LABEL_RE.test(x.seat)) seats[x.seat] = p.id;
  }
  return { provider: 'claude', shared, current, seats, followers, terms_ack: termsAcknowledged(opts.dataDir), terms_text: TERMS_LINE, accounts };
}

// ---- running the switch --------------------------------------------------------------------------

export interface CliResult { code: number | null; stdout: string; timedOut: boolean; error?: string }
export type RunCli = (python: string, args: string[], env: NodeJS.ProcessEnv, timeoutMs: number) => Promise<CliResult>;

/** Runs the CLI. On the deadline it ANSWERS (timedOut) but never kills the child: a login move is never cut off
 *  half-way (the CLI waits at most ~40 s for Claude's locks, then acts in milliseconds or refuses). */
export const runCli: RunCli = (python, args, env, timeoutMs) => new Promise((resolve) => {
  let stdout = '';
  let done = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const finish = (r: CliResult) => { if (!done) { done = true; if (timer) clearTimeout(timer); resolve(r); } };
  let child: ReturnType<typeof spawn>;
  try {
    child = spawn(python, args, { env, stdio: ['ignore', 'pipe', 'ignore'] });
  } catch (e: any) {
    finish({ code: null, stdout: '', timedOut: false, error: String(e?.code || e?.message || e) });
    return;
  }
  timer = setTimeout(() => finish({ code: null, stdout, timedOut: true }), timeoutMs);
  child.stdout?.on('data', (c) => { if (stdout.length < 1 << 20) stdout += String(c); });
  child.on('error', (e: any) => finish({ code: null, stdout, timedOut: false, error: String(e?.code || e?.message || e) }));
  child.on('close', (code) => finish({ code, stdout, timedOut: false }));
});

export function switchCliPath(env: NodeJS.ProcessEnv = process.env): string {
  return env.ORCHESTRA_ACCOUNT_SWITCH || join(homedir(), 'scripts', 'agent-orchestra', 'scripts', 'account_switch.py');
}

export const HOLDERS_TIMEOUT_MS = 15_000;

const providerArgs = (provider: Provider | undefined) => (provider === 'codex' ? ['--provider', 'codex'] : []);

/** account_switch.py --holders: {account: {where, seat, path, live_pids}}, or null when it cannot be asked.
 *  provider "codex": --provider codex --holders -> {account: {where, email, name, agents}}. */
export async function readHolders(opts: { dataDir: string; run?: RunCli; python?: string; cli?: string; timeoutMs?: number;
  provider?: Provider }): Promise<Holders | null> {
  const env = { ...process.env, ORCHESTRA_DIR: opts.dataDir };
  delete (env as any).TMUX;
  const r = await (opts.run ?? runCli)(opts.python ?? 'python3', [opts.cli ?? switchCliPath(), ...providerArgs(opts.provider), '--holders'],
    env, opts.timeoutMs ?? HOLDERS_TIMEOUT_MS);
  if (r.timedOut || r.code !== 0) return null;
  const lines = r.stdout.split('\n').map((l) => l.trim()).filter(Boolean);
  try {
    const j = JSON.parse(lines[lines.length - 1] ?? '');
    return j && typeof j === 'object' && !Array.isArray(j) && Object.values(j).every((v) => v && typeof v === 'object') ? j : null;
  } catch { return null; }
}

/** The cards, as GET /api/usage/accounts serves them. */
export async function loadAccountsView(opts: { dataDir: string; run?: RunCli; cli?: string; home?: string }): Promise<AccountsView | null> {
  if (enrolledClaude(opts.dataDir).length < 2) return null;              // one login: never even ask
  return accountsView({ dataDir: opts.dataDir, home: opts.home, holders: await readHolders(opts) });
}

/** The one JSON line the CLI prints (the last non-empty line), or null. */
export function parseCliLine(stdout: string): any {
  const lines = stdout.split('\n').map((s) => s.trim()).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    try { const j = JSON.parse(lines[i]); if (j && typeof j === 'object' && typeof j.status === 'string') return j; } catch { /* next */ }
  }
  return null;
}

const SHARED_SIGNED_OUT = 'The login all agents use now was signed out by Claude (a refresh failed), so it could not be parked. '
  + 'Sign in again before switching; nothing was changed.';

/** Plain words for every refusal the switch core can give. A test reads the core's codes and requires an entry for each. */
export function refusalWords(to: string, account: string, pids = ''): Record<string, string> {
  return {
    'account-live-at-home': `${to} is signed in in another Claude session right now${pids ? ` (pid ${pids})` : ''}. Close that session, `
      + 'then press again. A login is never copied, so it can only be in one place.',
    'account-on-another-seat': `One agent holds ${to}'s login in its own folder. It must give it back before all agents can use it.`,
    'account-on-the-shared-dir': `${to} is already the login all agents use.`,
    'account-is-the-fleet-login': `${to} is the main login; it never leaves the shared folder this way.`,
    'account-not-enrolled': `No Claude account is called ${account}.`,
    'bad-label': `No Claude account is called ${account}.`,
    'shared-login-unnamed': 'The registry does not say which account the main login is, so nothing could be parked; nothing was changed.',
    'cli-lock-busy': 'Claude is refreshing a login right now. Nothing was changed; try again in a minute.',
    'registry-lock-busy': 'The account registry is busy. Nothing was changed; try again.',
    'pending-switch': 'An earlier switch stopped half-way. Nothing will move until someone checks the switch ledger.',
    'vault-slot-occupied': 'A parked copy of the login in use is already in its vault slot; nothing was overwritten or changed.',
    'symlink-refused': 'A login file or folder is a link, not a plain file; nothing was changed.',
    'not-a-regular-file': 'A login file is not a plain file; nothing was changed.',
    'login-missing': `${to}'s login is not where the ledger says; nothing was changed.`,
    'login-unreadable': `${to}'s login file cannot be read; nothing was changed.`,
    'login-empty': `${to}'s login is signed out (Claude clears a login whose refresh failed). It needs a fresh /login in that account; nothing was changed.`,
    'outgoing-login-blank': SHARED_SIGNED_OUT,
    'sign-in-needed': SHARED_SIGNED_OUT,
    'account-login-dead': `${to} was signed out by Claude (a refresh failed). It needs a fresh /login in that account before it can be used; nothing was changed.`,
    'no-source-label': `The registry does not say which account ${to}'s login is; nothing was changed.`,
    'seat-login-unnamed': 'The registry does not say which account that agent\'s login is; nothing was changed.',
    'no-canonical-seat': 'That agent is not running right now; nothing was changed.',
    'no-live-pane': 'That agent is not running right now; nothing was changed.',
    'no-claude-process': 'That agent has no Claude process running right now; nothing was changed.',
    'mixed-config-dirs': 'That agent runs Claude from more than one login folder, so it is unclear which to switch; nothing was changed.',
    'account-already-held': `${to}'s login already has a holder; nothing was changed.`,
    'account-still-in-vault': `${to}'s login is still parked in its vault slot; nothing was changed.`,
    'registry-unreadable': 'The account registry cannot be read; nothing was changed.',
    'config-json-unparsable': 'Claude\'s settings file for the login in use cannot be read, so its account label could not follow; nothing was changed.',
    'config-json-missing': 'Claude\'s settings file for the login in use is missing, so its account label could not follow; nothing was changed.',
    'label-splice-mismatch': 'The account label could not be moved without touching anything else in Claude\'s settings file; nothing was changed.',
  };
}

/** Plain words for every refusal the Codex switch (--provider codex --shared) can give. */
export function codexRefusalWords(to: string, account: string): Record<string, string> {
  return {
    'account-not-enrolled': `No Codex account is called ${account}.`,
    'bad-label': `No Codex account is called ${account}.`,
    'login-missing': `${to}'s Codex login is not in its vault slot; nothing was changed.`,
    'login-unreadable': `${to}'s Codex login file cannot be read; nothing was changed.`,
    'login-empty': `${to}'s Codex login is signed out. It needs a fresh Codex sign-in in that account; nothing was changed.`,
    'outgoing-login-blank': 'The Codex login all Codex agents use now is signed out, so it could not be parked. '
      + 'Sign in to Codex again before switching; nothing was changed.',
    'shared-login-unnamed': 'It is not known which account the Codex login in use is, so nothing could be parked; nothing was changed.',
    'vault-slot-occupied': 'A parked copy of the Codex login in use is already in its vault slot; nothing was overwritten or changed.',
    'registry-lock-busy': 'The account registry is busy. No Codex login was changed; try again.',
    'symlink-refused': 'A Codex login file or folder is a link, not a plain file; nothing was changed.',
    'not-a-regular-file': 'A Codex login file is not a plain file; nothing was changed.',
    'vault-slot-in-use': `A Codex process is running on ${to}'s parked login right now (a check or a review); nothing was changed. Try again in a minute.`,
    'registry-unreadable': 'The account registry cannot be read; no Codex login was changed.',
    // raised after the login exchange can have happened: never "nothing was changed"
    'pending-switch': 'The Codex switch stopped half-way. Codex agents may already be on the new login; the next press tries to finish it. '
      + 'Check logs/account-switch.jsonl before pressing again.',
    'io-error': 'The Codex switch hit a file error and may have stopped half-way; the next press finishes or repairs it. '
      + 'Check logs/account-switch.jsonl before pressing again.',
  };
}

/** Where the replaced login went (the core's displaced_to), in plain words. Cores before 7a5b68205 send none and always
 * parked. An unknown value is never echoed. */
export function displacedWords(from: string, where: unknown): string {
  if (where === 'home') return `${from}'s login went back to its own folder; press its card to switch back (close any Claude session using that folder first).`;
  if (where === 'vault' || where === undefined) return `${from} is parked; press its card to switch back.`;
  if (typeof where === 'string' && where.startsWith('nothing')) {
    return `${from} had already been signed out by Claude, so nothing was parked. It needs a fresh /login before it can be used again.`;
  }
  return `${from}'s card shows where its login is now.`;
}

export interface SwitchAnswer { http: number; body: { ok: boolean; status: string; reason?: string; account: string; from?: string | null; message: string } }

/** The CLI's answer in the operator's words. Never passes on a path, a fingerprint or the CLI's detail text. */
export function switchAnswer(account: string, r: CliResult, names: Record<string, string> = {}, provider: Provider = 'claude'): SwitchAnswer {
  const nm = (id: string | null | undefined) => (id && names[id]) || id || 'that account';
  const to = nm(account);
  const refused = (reason: string, message: string, http = 409): SwitchAnswer =>
    ({ http, body: { ok: false, status: 'refused', reason, account, message } });
  if (r.timedOut) {
    return { http: 504, body: { ok: false, status: 'unknown', reason: 'timeout', account,
      message: 'The switch did not answer within a minute. It may still have happened: the cards re-read where each login is. '
        + 'Check logs/account-switch.jsonl before pressing again.' } };
  }
  const out = parseCliLine(r.stdout);
  if (!out) {
    return { http: 502, body: { ok: false, status: 'unknown', reason: r.error ? 'cli-not-run' : 'cli-no-answer', account,
      message: r.error ? 'The account switch could not be started on this server; nothing was changed.'
        : 'The account switch gave no answer. The cards re-read where each login is; check logs/account-switch.jsonl.' } };
  }
  if (r.code === 0 && out.status === 'switched' && provider === 'codex') {
    const from = nm(typeof out.from === 'string' ? out.from : null);
    return { http: 200, body: { ok: true, status: 'switched', account, from: typeof out.from === 'string' ? out.from : null,
      message: `New Codex sessions and reviews use ${to} from now on. Running Codex agents keep ${from} until their next start. `
        + `${from} is parked; press its card to switch back.` } };
  }
  if (r.code === 0 && out.status === 'switched') {
    const f = typeof out.followers === 'number' ? out.followers : null;
    return { http: 200, body: { ok: true, status: 'switched', account, from: typeof out.from === 'string' ? out.from : null,
      message: `All agents on the main login${f !== null ? ` (${plural(f, 'Claude process', 'Claude processes')})` : ''} switch to ${to} `
        + `at their next model call. Nothing restarts. ${displacedWords(nm(out.from), out.displaced_to)} `
        + 'An agent’s /status can keep showing the old account for a while: this page shows the right one.' } };
  }
  if (r.code === 0 && out.status === 'noop') {
    return { http: 200, body: { ok: true, status: 'noop', account, from: account,
      message: provider === 'codex' ? `${to} is already the login all Codex agents use.` : `${to} is already the login all agents use.` } };
  }
  if (out.status !== 'refused') return refused('unexpected', `The account switch answered "${String(out.status).slice(0, 40)}"; nothing was assumed.`, 502);
  const reason = typeof out.reason === 'string' ? out.reason : 'refused';
  const detail = typeof out.detail === 'string' ? out.detail : '';
  const pids = (/pid ([0-9, ]+)/.exec(detail)?.[1] || '').trim().replace(/,\s*/g, ', ');
  const M = provider === 'codex' ? codexRefusalWords(to, account) : refusalWords(to, account, pids);
  const busy = reason === 'cli-lock-busy' || reason === 'registry-lock-busy';
  const words = Object.prototype.hasOwnProperty.call(M, reason) ? M[reason]
    : `Not switched (${/^[a-z][a-z-]{0,40}$/.test(reason) ? reason : 'a reason this page does not know'}). Nothing was changed.`;
  return refused(reason, words, busy ? 503 : 409);
}

/** One switch at a time from this API (the CLI's own lock serialises it with every other writer too). */
let chain: Promise<unknown> = Promise.resolve();
export function serialised<T>(fn: () => Promise<T>): Promise<T> {
  const p = chain.then(fn, fn);
  chain = p.catch(() => undefined);
  return p;
}

export async function switchAllAgents(account: string, opts: { dataDir: string; run?: RunCli; python?: string; cli?: string;
  timeoutMs?: number; names?: Record<string, string>; provider?: Provider }): Promise<SwitchAnswer> {
  const run = opts.run ?? runCli;
  const env = { ...process.env, ORCHESTRA_DIR: opts.dataDir };
  delete (env as any).TMUX;
  const r = await run(opts.python ?? 'python3', [opts.cli ?? switchCliPath(), ...providerArgs(opts.provider), '--shared', account], env,
    opts.timeoutMs ?? SWITCH_TIMEOUT_MS);
  return switchAnswer(account, r, opts.names, opts.provider ?? 'claude');
}

// ---- Codex: the same button for the fleet's ~/.codex login ----------------------------------------------

export interface CodexAccountCard {
  id: string;
  /** The switch's name for it ("shaw.cole"), else its id. */
  name: string;
  email: string | null;
  /** shared: the login in ~/.codex all Codex agents use now; vault: parked; missing: not in its vault slot. */
  where: 'shared' | 'vault' | 'missing';
  /** Running Codex processes on it (0 unless shared). */
  agents: number;
  canUse: boolean;
  whyNot: string | null;
  /** "5-hour at 93%, resets 10:49 PM Tulum" when a usage window is at 90% or more (state/usage/api-codex-<id>.json);
   *  a heads-up only, never a refusal. */
  note: string | null;
}

const CODEX_WINDOWS: Array<[string, string]> = [['5h', '5-hour'], ['weekly', 'weekly']];

/** The heads-up line for a Codex card from its usage file, or null (no file, unreadable, or every window under 90%). */
export function codexUsageNote(dataDir: string, id: string, now?: Date): string | null {
  if (!LABEL_RE.test(id)) return null;
  const d = readJson(join(dataDir, 'state', 'usage', `api-codex-${id}.json`));
  const w = d && typeof d.windows === 'object' && d.windows ? d.windows : null;
  if (!w) return null;
  const parts: string[] = [];
  for (const [key, label] of CODEX_WINDOWS) {
    const x = w[key];
    const pct = x && typeof x.used_pct === 'number' && Number.isFinite(x.used_pct) ? x.used_pct : null;
    if (pct === null || pct < 90) continue;
    const at = x && typeof x.resets_at === 'number' && Number.isFinite(x.resets_at) ? timeText(new Date(x.resets_at * 1000).toISOString(), shawZone(dataDir), now) : null;
    parts.push(`${label} at ${Math.round(pct)}%${at ? `, resets ${at}` : ''}`);
  }
  return parts.length ? parts.join('; ') : null;
}

export interface CodexAccountsView {
  provider: 'codex';
  current: CurrentLogin | null;
  /** Always {}: every Codex agent shares ~/.codex. */
  seats: Record<string, string>;
  /** The account whose login ~/.codex holds now (null when --holders did not answer or names none). */
  shared: string | null;
  followers: number;
  terms_ack: boolean;
  terms_text: string;
  accounts: CodexAccountCard[];
}

const CODEX_UNREADABLE = 'Where each login is cannot be read right now.';

/** The Codex cards from `--provider codex --holders`. null with fewer than two Codex accounts. holders null: the
 *  registry's enrolled Codex profiles (if any) are listed, every button disabled, saying so. */
export function codexAccountsView(opts: { dataDir: string; holders: Holders | null; now?: Date }): CodexAccountsView | null {
  const H = opts.holders && typeof opts.holders === 'object' ? (opts.holders as Record<string, any>) : null;
  let accounts: CodexAccountCard[];
  if (H) {
    accounts = Object.keys(H).filter((id) => LABEL_RE.test(id) && H[id] && typeof H[id] === 'object').map((id) => {
      const x = H[id];
      const name = typeof x.name === 'string' && x.name.trim() && x.name.trim().length <= 64 ? x.name.trim() : id;
      const email = typeof x.email === 'string' && x.email ? x.email : null;
      const agents = Number.isInteger(x.agents) && x.agents > 0 ? x.agents as number : 0;
      const card: CodexAccountCard = { id, name, email, where: 'missing', agents: 0, canUse: false, whyNot: null,
        note: codexUsageNote(opts.dataDir, id, opts.now) };
      if (x.where === 'shared') return { ...card, where: 'shared', agents, whyNot: 'This is the login all Codex agents use now.' };
      if (x.where === 'vault') return { ...card, where: 'vault', canUse: true };
      if (x.where === 'missing') return { ...card, whyNot: 'This login is not in its vault slot; nothing to switch to.' };
      return { ...card, whyNot: 'The switch reports this login somewhere this page does not know.' };
    });
  } else {
    const reg = readJson(join(opts.dataDir, 'state', 'claude_profiles.json'));
    const list = reg && Array.isArray(reg.profiles) ? reg.profiles : [];
    accounts = list.filter((p: any) => p && p.enrolled === true && p.provider === 'codex' && typeof p.id === 'string' && LABEL_RE.test(p.id))
      .map((p: any) => ({ id: p.id, name: displayName(p), email: emailOf(p), where: 'missing' as const, agents: 0, canUse: false, whyNot: CODEX_UNREADABLE,
        note: codexUsageNote(opts.dataDir, p.id, opts.now) }));
  }
  if (accounts.length < 2) return null;
  const sc = accounts.find((a) => a.where === 'shared') ?? null;
  let current: CurrentLogin | null = null;
  if (sc) {
    const since = lastFleetSwitch(opts.dataDir, sc.id, 'codex');
    current = { account: sc.id, name: sc.name, email: sc.email, since,
      since_text: since ? timeText(since, shawZone(opts.dataDir), opts.now) : null, by: switchedBy(opts.dataDir, sc.id, since, 'codex') };
  }
  return { provider: 'codex', shared: sc?.id ?? null, current, seats: {}, followers: sc?.agents ?? 0,
    terms_ack: termsAcknowledged(opts.dataDir, 'codex'), terms_text: CODEX_TERMS_LINE, accounts };
}

/** The Codex cards, as GET /api/usage/accounts?provider=codex serves them. */
export async function loadCodexAccountsView(opts: { dataDir: string; run?: RunCli; cli?: string; now?: Date }): Promise<CodexAccountsView | null> {
  return codexAccountsView({ dataDir: opts.dataDir, now: opts.now, holders: await readHolders({ ...opts, provider: 'codex' }) });
}
