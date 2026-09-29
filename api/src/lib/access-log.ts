/**
 * Forensic access logging for the API.
 *
 * Found during triage of the 2026-09-29 identity-spoofing P0: api.log contained ZERO
 * client addresses. The API had been bound to every interface with fail-open admin
 * defaults, and there was no way to answer the only question that mattered afterwards —
 * "did any of this come from another host?" The gateway logs peer addresses, which is why
 * triage was possible for that path at all; this surface had no equivalent.
 *
 * Deliberately NOT a log line per request. The dashboard polls constantly, and burying the
 * interesting events in GET noise is how a log becomes unreadable and then unread. One line
 * is emitted only when a request is forensically interesting:
 *
 *   1. it came from a non-loopback address — after the bind fix this should never happen,
 *      so any such line is an alarm, not a data point;
 *   2. it carries any X-Orchestra-* identity header — nothing in this repo sets these, so
 *      their presence is either a real proxy being introduced or someone trying the spoof;
 *   3. it mutates state (any method other than GET/HEAD/OPTIONS) — writes are what you
 *      need attributed after an incident, and they are a small fraction of traffic.
 *
 * Header VALUES for the identity headers are logged, per the request: they are claims, not
 * secrets, and the claim is the evidence. Authorization and Cookie are recorded as presence
 * only — logging those would turn an audit log into a credential store.
 */
import type { Request, Response, NextFunction } from 'express';

const IDENTITY_PREFIX = 'x-orchestra-';
const SECRET_HEADERS = new Set(['authorization', 'cookie', 'proxy-authorization']);
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/** Loopback in v4, v6, and the v4-mapped-v6 form Node reports on a dual-stack socket. */
export function isLoopback(addr: string | undefined): boolean {
  if (!addr) return false;
  const a = addr.replace(/^::ffff:/, '');
  return a === '127.0.0.1' || a === '::1' || a.startsWith('127.');
}

export function clientAddr(req: Request): string {
  return (req.socket && req.socket.remoteAddress) || 'unknown';
}

export interface AccessEvent {
  addr: string;
  method: string;
  path: string;
  identity: Record<string, string>;
  /** Claimed upstream chain. Recorded as a CLAIM — never trusted, never used for control. */
  forwardedFor: string | null;
  secretsPresent: string[];
  reasons: string[];
}

export function describe(req: Request): AccessEvent | null {
  const addr = clientAddr(req);
  const identity: Record<string, string> = {};
  const secretsPresent: string[] = [];

  for (const [k, v] of Object.entries(req.headers)) {
    const key = k.toLowerCase();
    if (key.startsWith(IDENTITY_PREFIX)) {
      identity[key] = Array.isArray(v) ? v.join(',') : String(v ?? '');
    } else if (SECRET_HEADERS.has(key)) {
      secretsPresent.push(key);
    }
  }

  const reasons: string[] = [];
  if (!isLoopback(addr)) reasons.push('non-loopback');
  if (Object.keys(identity).length) reasons.push('identity-headers');
  if (!SAFE_METHODS.has((req.method || '').toUpperCase())) reasons.push('mutating');
  if (!reasons.length) return null;

  const xff = req.headers['x-forwarded-for'];
  return {
    addr,
    method: req.method,
    path: (req.originalUrl || req.url || '').split('?')[0],
    identity,
    forwardedFor: xff ? (Array.isArray(xff) ? xff.join(',') : String(xff)) : null,
    secretsPresent,
    reasons,
  };
}

export function format(e: AccessEvent): string {
  const bits = [
    `addr=${e.addr}`,
    `method=${e.method}`,
    `path=${e.path}`,
    `why=${e.reasons.join('+')}`,
  ];
  for (const [k, v] of Object.entries(e.identity)) bits.push(`${k}=${JSON.stringify(v)}`);
  if (e.forwardedFor) bits.push(`x-forwarded-for(claimed)=${JSON.stringify(e.forwardedFor)}`);
  if (e.secretsPresent.length) bits.push(`credentials_present=${e.secretsPresent.join(',')}`);
  return `[access ${new Date().toISOString()}] ${bits.join(' ')}`;
}

export function accessLog(req: Request, _res: Response, next: NextFunction): void {
  try {
    const e = describe(req);
    if (e) console.log(format(e));
  } catch {
    // Logging must never break a request.
  }
  next();
}
