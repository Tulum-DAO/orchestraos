/**
 * Did the local combo proxy vouch for this request? The API-side twin of dashboard-proxy.js comboProxyVouched():
 * combo-proxy (:5053) authenticates a tailnet caller, then stamps `x-orchestra-proxy-auth: <shared secret>` plus its
 * own x-orchestra-user / x-orchestra-role, and the dashboard proxy forwards the headers to this API as they are.
 *
 * A bare `x-orchestra-user` proves nothing (any client can send it, and the dashboard proxy passes client headers
 * through), so the secret is what is checked: timing-safe, against the same file combo reads
 * (ORCHESTRA_PROXY_AUTH_FILE, default ~/.secrets/orchestra-proxy-auth.secret). A secret file that group or others
 * can read is ignored (as lib/proxy_identity.cjs does), and so is any request that did not come from loopback.
 * Read per call, so a rotated secret applies at once and a test can point the env at a fixture.
 */
import { readFileSync, statSync } from 'fs';
import { homedir } from 'os';
import { join } from 'path';
import { timingSafeEqual } from 'crypto';
import type { IncomingMessage } from 'http';
import { isLoopback } from './tailscale-identity.js';

export function proxyAuthFile(env: NodeJS.ProcessEnv = process.env): string {
  return env.ORCHESTRA_PROXY_AUTH_FILE || join(homedir(), '.secrets', 'orchestra-proxy-auth.secret');
}

function readSecret(file: string): string {
  try {
    if (statSync(file).mode & 0o077) return '';
    return readFileSync(file, 'utf8').trim();
  } catch { return ''; }
}

const header = (req: IncomingMessage, name: string): string => {
  const v = req.headers[name];
  return String((Array.isArray(v) ? v[0] : v) || '').trim();
};

/** Loopback peer, a named user, and the proxy secret, timing-safe. */
export function comboProxyVouched(req: IncomingMessage, file = proxyAuthFile()): boolean {
  if (!isLoopback(req.socket?.remoteAddress)) return false;
  const got = header(req, 'x-orchestra-proxy-auth');
  if (!got || !header(req, 'x-orchestra-user')) return false;
  const want = readSecret(file);
  if (!want) return false;
  const a = Buffer.from(got);
  const b = Buffer.from(want);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Vouched for AND an admin by combo's own role header. */
export function comboProxyAdmin(req: IncomingMessage, file = proxyAuthFile()): boolean {
  return comboProxyVouched(req, file) && header(req, 'x-orchestra-role') === 'admin';
}
