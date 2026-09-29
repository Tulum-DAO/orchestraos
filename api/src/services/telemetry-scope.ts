/**
 * telemetry-scope.ts — per-session tenant scoping for the telemetry endpoints.
 *
 * BLOCKING-3 (DEC-1788461603): the STREAM endpoints must 403 a scoped user
 * opening a session outside their client BEFORE any court-bridge call or delta
 * relay — not only the REST /status list. This mirrors agents.ts exactly:
 *   - x-orchestra-client=<scope>          => only agents tagged client:<scope>
 *   - x-orchestra-allowed-agents=[ids]     => only those ids/sessions
 *   - admin ('*', no client scope)         => all
 * An unknown session (not in the registry) is DENIED for a scoped user (cannot
 * prove the tag) and allowed for admin.
 */
import type { Request } from 'express';
import { getRegistry } from './state-reader.js';
import { principal } from '../lib/principal.js';

interface TenantCtx { clientScope: string; allowed: '*' | Set<string> }

export function tenantCtx(req: Request): TenantCtx {
  // Scope from the principal, not raw headers: untrusted mode yields '*' (unchanged
  // behaviour), trusted mode yields [] when the proxy asserted none — fail closed.
  const p = principal(req);
  const clientScope = p?.clientScope || '';
  const allowedAgents = p?.allowedAgents ?? [];
  if (clientScope) return { clientScope, allowed: '*' };
  if (allowedAgents === '*') return { clientScope: '', allowed: '*' };
  const ids = Array.isArray(allowedAgents)
    ? allowedAgents.map(String)
    : String(allowedAgents).split(',');
  return { clientScope: '', allowed: new Set(ids.map((s) => s.trim()).filter(Boolean)) };
}

/** Find a registry agent record for a session (by key or tmux_session). */
function agentRecordFor(session: string): Record<string, unknown> | null {
  const reg = getRegistry() as Record<string, unknown> | null;
  const agents = (reg?.agents ?? {}) as Record<string, Record<string, unknown>>;
  if (agents[session]) return agents[session];
  for (const rec of Object.values(agents)) {
    if (rec && (rec.tmux_session === session || rec.name === session)) return rec;
  }
  return null;
}

function hasClientTag(rec: Record<string, unknown>, scope: string): boolean {
  if (rec.client === scope) return true;
  const tags = Array.isArray(rec.tags) ? (rec.tags as unknown[]).map(String) : [];
  return tags.includes(`client:${scope}`);
}

/** True iff the requester may see this session's telemetry. */
export function canAccessSession(req: Request, session: string): boolean {
  const ctx = tenantCtx(req);
  if (ctx.clientScope) {
    const rec = agentRecordFor(session);
    return !!rec && hasClientTag(rec, ctx.clientScope);   // unknown => deny
  }
  if (ctx.allowed === '*') return true;                    // admin
  // Legacy explicit list: match session directly or its registry key/id.
  if (ctx.allowed.has(session)) return true;
  const reg = getRegistry() as Record<string, unknown> | null;
  const agents = (reg?.agents ?? {}) as Record<string, Record<string, unknown>>;
  for (const [id, rec] of Object.entries(agents)) {
    if ((rec?.tmux_session === session || rec?.name === session) && ctx.allowed.has(id)) return true;
  }
  return false;
}

/** Filter a session->seat map to the sessions the requester may see. */
export function scopeSeats<T>(req: Request, seats: Record<string, T>): Record<string, T> {
  const ctx = tenantCtx(req);
  if (!ctx.clientScope && ctx.allowed === '*') return seats;
  const out: Record<string, T> = {};
  for (const [session, seat] of Object.entries(seats)) {
    if (canAccessSession(req, session)) out[session] = seat;
  }
  return out;
}
