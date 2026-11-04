// The dashboard is reached through Tailscale Serve, which stamps every tailnet request with the
// visitor's identity (Tailscale-User-Login) and strips any copy the client sent. No install issues an
// orchestra_session cookie, so without this the voice call and the terminal refused everyone. A
// request counts as the operator when it arrived from loopback (Serve -> dashboard proxy -> here) and
// its login is on the allowlist: ORCHESTRA_TAILSCALE_USERS (comma-separated), else
// <data dir>/state/tailscale-users (one login per line, optionally followed by its role: admin or member). No allowlist = nobody, never everybody.
import { readFileSync } from 'fs';
import { join } from 'path';
import type { IncomingMessage } from 'http';
import { loadConfig } from './config.js';

export type InstallRole = 'admin' | 'member';
export interface TailscaleUser { login: string; role: InstallRole | null }
const ROLE_WORDS: ReadonlySet<string> = new Set(['admin', 'member']);

/** `login [role]` per line (the role is optional; none means admin), or the old comma/space-separated list. `#` starts a
 *  comment. A login is a word with an `@` in it. Any other word belongs to the login before it on the same line (or, with
 *  none before it, the next one: `member a@x` works), and is never a login itself:
 *    admin / member   that login's role
 *    anything else    a typo or an unknown role: that login is a member (least privilege), and it is logged
 *  A login listed more than once takes the least privilege of its lines: any `member` wins.
 *  dashboard-tailscale-users.js (the web terminal's allowlist) follows the same rule for which words are logins. */
const warned = new Set<string>();
function warnOnce(msg: string): void {
  if (warned.has(msg)) return;
  warned.add(msg);
  console.warn(msg);
}

const leastPrivilege = (a: InstallRole | null, b: InstallRole | null): InstallRole | null =>
  a === 'member' || b === 'member' ? 'member' : a === 'admin' || b === 'admin' ? 'admin' : null;

export function parseTailscaleUsers(raw: string): TailscaleUser[] {
  const byLogin = new Map<string, TailscaleUser>();
  for (const line of raw.split(/\r?\n/)) {
    let last: TailscaleUser | null = null;
    let pending: InstallRole | null = null;
    let pendingWord = '';
    for (const tok of line.replace(/#.*$/, '').split(/[\s,]+/).map((s) => s.trim().toLowerCase()).filter(Boolean)) {
      if (ROLE_WORDS.has(tok) || !tok.includes('@')) {
        const role: InstallRole = ROLE_WORDS.has(tok) ? (tok as InstallRole) : 'member';
        if (!ROLE_WORDS.has(tok)) {
          if (last) warnOnce(`[tailscale-users] "${tok}" after ${last.login} is not a role (admin or member): ${last.login} is a member`);
          else pendingWord = tok;
        }
        if (last) last.role = leastPrivilege(last.role, role);
        else pending = leastPrivilege(pending, role);
        continue;
      }
      let u = byLogin.get(tok);
      if (!u) { u = { login: tok, role: null }; byLogin.set(tok, u); }
      if (pending) {
        u.role = leastPrivilege(u.role, pending);
        if (pendingWord) warnOnce(`[tailscale-users] "${pendingWord}" before ${tok} is not a role (admin or member): ${tok} is a member`);
        pending = null;
        pendingWord = '';
      }
      last = u;
    }
    if (pendingWord) warnOnce(`[tailscale-users] "${pendingWord}" is not a login (no @) and has no login on its line: ignored`);
  }
  return [...byLogin.values()];
}

function rawTailscaleUsers(): string {
  const env = process.env.ORCHESTRA_TAILSCALE_USERS || '';
  if (env) return env;
  try { return readFileSync(join(loadConfig().dataDir, 'state', 'tailscale-users'), 'utf-8'); } catch { return ''; }
}

export function tailscaleUsers(): TailscaleUser[] {
  return parseTailscaleUsers(rawTailscaleUsers());
}

export function allowedTailscaleUsers(): string[] {
  return tailscaleUsers().map((u) => u.login);
}

/** The install role of a Tailscale login: its role in tailscale-users, and a listed login with no role is admin (so
 *  nothing changes for an install whose file names no roles). A login NOT in the file is a member: never admin
 *  ("Always member"). */
export function tailscaleRole(login: string, users: TailscaleUser[] = tailscaleUsers()): InstallRole {
  const l = login.trim().toLowerCase();
  const u = users.find((x) => x.login === l);
  return u ? u.role ?? 'admin' : 'member';
}

export function isLoopback(addr: string | undefined): boolean {
  return addr === '127.0.0.1' || addr === '::1' || addr === '::ffff:127.0.0.1';
}

export function tailscaleOperator(req: IncomingMessage, allowed = allowedTailscaleUsers()): boolean {
  if (!isLoopback(req.socket?.remoteAddress)) return false;
  const login = String(req.headers['tailscale-user-login'] || '').trim().toLowerCase();
  return !!login && allowed.includes(login);
}
