/**
 * Which agents a principal may see — ONE rule, used by GET / and by every /:id route.
 *
 * GET / narrowed the fleet by principal scope ("fail closed, not wide open"), but the /:id
 * routes beside it had no check at all: 13 in agents.ts plus /:id/transcript,
 * /:id/transcript/stream and /:id/send, nine of them mutating. Under a trusted proxy, a
 * principal scoped to one client could read, kill or type into any agent by id. The rule lives
 * here so the list and the per-id routes cannot drift apart again, and it is applied through
 * router.param('id') so a /:id route added later is scoped without anyone remembering to.
 *
 * In the default untrusted mode principal() is the configured operator with '*', so this is a
 * no-op there. See agent-scope.test.ts for both modes.
 */
import type { NextFunction, Request, Response } from 'express';
import { principal, type Principal } from './principal.js';
import { getAgentState, getRegistry } from '../services/state-reader.js';

/** Tags as GET / coerces them: an array as-is, a comma string split, anything else none. */
export function coerceTags(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String);
  if (typeof v === 'string') return v.split(',').map((s) => s.trim()).filter(Boolean);
  return [];
}

/**
 * The GET / rule, verbatim in behaviour:
 * - no principal (trusted mode, no identity asserted) sees nothing;
 * - a client scope sees exactly the agents tagged `client:<scope>` — checked FIRST, so an id
 *   in an allowlist cannot leak past a client scope;
 * - otherwise '*' sees everything, and an allowlist (array or comma string) sees its members.
 */
export function canPrincipalSeeAgent(
  p: Principal | null,
  agent: { id: string; tags?: unknown },
): boolean {
  const clientScope = p?.clientScope || '';
  const allowedAgents = p?.allowedAgents ?? [];
  if (clientScope) return coerceTags(agent.tags).includes(`client:${clientScope}`);
  if (allowedAgents === '*') return true;
  const allowed = new Set(
    (Array.isArray(allowedAgents) ? allowedAgents : String(allowedAgents).split(','))
      .map((s) => String(s).trim())
      .filter(Boolean),
  );
  return allowed.has(agent.id);
}

/**
 * Tags for one id, merged as GET / merges them: `{...def, ...state}`, so a state file's tags win
 * over the registry's. Unknown ids have no tags, which a client scope correctly refuses.
 */
export function defaultResolveTags(id: string): unknown {
  const registry = getRegistry() as { agents?: Record<string, Record<string, unknown>> } | null;
  const def = registry?.agents?.[id] ?? {};
  const merged = { ...def, ...getAgentState(id) };
  return merged.tags;
}

/**
 * router.param('id', ...) handler. Refuses an out-of-scope id with the SAME 404 GET /:id gives
 * an unknown one — not a 403 — so no route can be used to learn which ids exist. Refusal
 * happens before the handler runs: for kill and inject, that ordering is the fix.
 */
export function makeAgentScopeParam(resolveTags: (id: string) => unknown = defaultResolveTags) {
  return (req: Request, res: Response, next: NextFunction, id: string): void => {
    if (canPrincipalSeeAgent(principal(req), { id, tags: resolveTags(id) })) {
      next();
      return;
    }
    res.status(404).json({ error: `Agent '${id}' not found` });
  };
}

export const agentScopeParam = makeAgentScopeParam();
