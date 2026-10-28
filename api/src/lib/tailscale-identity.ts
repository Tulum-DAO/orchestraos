// The dashboard is reached through Tailscale Serve, which stamps every tailnet request with the
// visitor's identity (Tailscale-User-Login) and strips any copy the client sent. No install issues an
// orchestra_session cookie, so without this the voice call and the terminal refused everyone. A
// request counts as the operator when it arrived from loopback (Serve -> dashboard proxy -> here) and
// its login is on the allowlist: ORCHESTRA_TAILSCALE_USERS (comma-separated), else
// <data dir>/state/tailscale-users (one login per line). No allowlist = nobody, never everybody.
import { readFileSync } from 'fs';
import { join } from 'path';
import type { IncomingMessage } from 'http';
import { loadConfig } from './config.js';

export function allowedTailscaleUsers(): string[] {
  let raw = process.env.ORCHESTRA_TAILSCALE_USERS || '';
  if (!raw) {
    try { raw = readFileSync(join(loadConfig().dataDir, 'state', 'tailscale-users'), 'utf-8'); } catch { raw = ''; }
  }
  return raw.split(/[\s,]+/).map((s) => s.trim().toLowerCase()).filter((s) => s && !s.startsWith('#'));
}

export function isLoopback(addr: string | undefined): boolean {
  return addr === '127.0.0.1' || addr === '::1' || addr === '::ffff:127.0.0.1';
}

export function tailscaleOperator(req: IncomingMessage, allowed = allowedTailscaleUsers()): boolean {
  if (!isLoopback(req.socket?.remoteAddress)) return false;
  const login = String(req.headers['tailscale-user-login'] || '').trim().toLowerCase();
  return !!login && allowed.includes(login);
}
