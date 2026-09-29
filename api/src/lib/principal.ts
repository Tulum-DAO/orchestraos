/**
 * Who is calling this API — one place, fail-closed.
 *
 * The vulnerability this replaces (2026-09-29, proven exploitable live): every route
 * derived identity inline as `req.headers['x-orchestra-user'] || operatorId`, with
 * `role || 'admin'` and `allowed-agents || '*'`. So an unauthenticated request header
 * granted admin with wildcard agent scope, and agent-send used the same header as the
 * msg_store from_agent — letting any local caller issue instructions attributed to gm.
 *
 * THIS MODULE IS NOT AUTHENTICATION. It cannot be: nothing in this repo authenticates
 * anyone. The headers were designed to be set by a trusted reverse proxy ("combo-proxy")
 * that is not present here — verified by grep, nothing sets them. So:
 *
 *   * DEFAULT (no proxy, today): identity headers are IGNORED ENTIRELY. Every caller is
 *     the configured operator. Spoofing becomes impossible rather than merely harder —
 *     `X-Orchestra-User: eve` has no effect at all — and the dashboard, which sends no
 *     headers, behaves exactly as before.
 *   * TRUSTED (ORCHESTRA_API_TRUST_IDENTITY_HEADERS=1, only correct when a proxy that
 *     authenticates and OVERWRITES these headers sits in front): headers are honoured and
 *     fail closed — absent role means no role, absent scope means empty scope, absent
 *     identity means no principal (callers must 401).
 *
 * The residual, which is a real limitation and not covered here: with the API bound to
 * loopback, anything running on this machine is the operator. Closing that needs a real
 * authenticated principal (a token the API verifies, or a proxy it can trust), which is a
 * design change, not a patch.
 */
import type { Request } from 'express';
import { loadConfig } from './config.js';

export interface Principal {
  username: string;
  role: string;
  /** '*' means every agent; an array is an explicit allowlist; [] means none. */
  allowedAgents: string | string[];
  clientScope: string | null;
  /** false when the identity was assumed from config rather than asserted by a proxy. */
  trusted: boolean;
}

/** Only true when an authenticating proxy is declared to be in front of this API. */
export function trustsIdentityHeaders(): boolean {
  const v = (process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS || '').trim().toLowerCase();
  return v === '1' || v === 'true' || v === 'yes' || v === 'on';
}

function header(req: Request, name: string): string {
  const v = req.headers[name];
  return (Array.isArray(v) ? v[0] : v || '').toString().trim();
}

/**
 * The calling principal, or null when identity is required but absent.
 * null NEVER means "admin" — callers must treat it as 401.
 */
export function principal(req: Request): Principal | null {
  if (!trustsIdentityHeaders()) {
    // No proxy to vouch for anyone: ignore what the client claims and use the configured
    // operator. This is the line that makes the reported exploit inert.
    return {
      username: loadConfig().operatorId,
      role: 'admin',
      allowedAgents: '*',
      clientScope: null,
      trusted: false,
    };
  }

  const username = header(req, 'x-orchestra-user');
  if (!username) return null;                       // fail closed: no identity, no access

  const role = header(req, 'x-orchestra-role');     // NO 'admin' default
  const clientScope = header(req, 'x-orchestra-client') || null;
  const allowedRaw = header(req, 'x-orchestra-allowed-agents');

  let allowedAgents: string | string[] = [];        // NO '*' default
  if (clientScope) {
    allowedAgents = clientScope;                    // tag-based: API filters by client tag
  } else if (allowedRaw === '*') {
    allowedAgents = '*';
  } else if (allowedRaw) {
    try {
      const parsed = JSON.parse(allowedRaw);
      allowedAgents = Array.isArray(parsed) ? parsed : allowedRaw.split(',').map(s => s.trim());
    } catch {
      allowedAgents = allowedRaw.split(',').map(s => s.trim()).filter(Boolean);
    }
  }

  return { username, role, allowedAgents, clientScope, trusted: true };
}

/** Tenant scoping, fail-closed: a caller with no role is never an admin. */
export function tenantScope(req: Request): { isAdmin: boolean; clientScope: string | null } {
  const p = principal(req);
  if (!p) return { isAdmin: false, clientScope: null };
  return { isAdmin: p.role === 'admin', clientScope: p.role === 'admin' ? null : p.clientScope };
}

/**
 * The agent id a write should be attributed to. NEVER a raw client header — that is what
 * allowed instructions to be posted as gm. Untrusted mode always yields the configured
 * operator; trusted mode yields the proxy-asserted principal.
 */
export function actingAgent(req: Request): string | null {
  const p = principal(req);
  return p ? p.username : null;
}
