/**
 * Who may see and act on which approval card — ONE rule, used by GET /api/approvals/unified, its
 * /stats, and every /:id route (approve, deny, answer).
 *
 * Found 2026-10-14: the approve/deny/answer routes had no check of their own, so under a trusted
 * identity proxy a principal scoped to one client could answer any card by id, including the
 * operator's. The list and stats showed every card to every caller too.
 *
 * The rule:
 * - no principal (trusted mode, no identity asserted): 401 on the /:id routes, nothing listed;
 * - an admin with no client scope and '*' agents: every card, every proposal (the operator);
 * - anyone else: only a PENDING canonical card whose asking agent they may see, by the same
 *   rule as /api/agents (lib/agent-scope: a client scope sees agents tagged client:<scope>, an
 *   allowlist its members). Learning proposals are fleet-wide, so admin-only.
 * An out-of-scope id gets the route's own 404, never a 403, so no route tells a caller which
 * ids exist.
 *
 * In the default untrusted mode principal() is the configured operator (admin, '*'), so nothing
 * changes there. See approval-scope.test.ts for both modes.
 */
import type { NextFunction, Request, Response } from 'express';
import { principal as defaultPrincipal, type Principal } from './principal.js';
import { canPrincipalSeeAgent, defaultResolveTags } from './agent-scope.js';

/** The operator: an admin that neither a client scope nor an agent allowlist narrows. */
export function isUnscopedAdmin(p: Principal | null): boolean {
  return !!p && p.role === 'admin' && !p.clientScope && p.allowedAgents === '*';
}

/** May this principal see (and so act on) a card asked by `agentId`? */
export function canPrincipalSeeCard(
  p: Principal | null,
  agentId: string | null | undefined,
  resolveTags: (id: string) => unknown = defaultResolveTags,
): boolean {
  if (!p) return false;
  if (isUnscopedAdmin(p)) return true;
  if (!agentId) return false;
  return canPrincipalSeeAgent(p, { id: agentId, tags: resolveTags(agentId) });
}

export interface ApprovalScopeDeps {
  principal: (req: Request) => Principal | null;
  /** The asking agent of a PENDING canonical card, or null when there is no such card. */
  pendingCardAgent: (id: string) => string | null;
  resolveTags: (id: string) => unknown;
}

/**
 * router.param('id', ...) handler for the /:id routes. Runs before the handler, so a refused
 * answer never reaches approval.py.
 */
export function makeApprovalScopeParam(deps: ApprovalScopeDeps) {
  return (req: Request, res: Response, next: NextFunction, id: string): void => {
    const p = deps.principal(req);
    if (!p) { res.status(401).json({ error: 'unauthenticated' }); return; }
    if (isUnscopedAdmin(p)) { next(); return; }
    if (canPrincipalSeeCard(p, deps.pendingCardAgent(id), deps.resolveTags)) { next(); return; }
    res.status(404).json({ error: `Approval item '${id}' not found in the canonical store` });
  };
}

export function defaultApprovalScopeDeps(pendingCardAgent: (id: string) => string | null): ApprovalScopeDeps {
  return { principal: defaultPrincipal, pendingCardAgent, resolveTags: defaultResolveTags };
}
