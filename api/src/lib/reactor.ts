// Who a reaction is from (spec "Reactor identity"). Built ON lib/principal.ts, never beside it:
//   - no principal (trusted-proxy mode, no identity asserted) -> null; the route answers 401, exactly like /api/me;
//   - X-Orchestra-Agent: <id> AND X-Orchestra-Agent-Token: <the token in state/agent-react-token> -> that agent;
//   - X-Orchestra-Agent present but the token missing/invalid -> AGENT_TOKEN_INVALID (the route answers 401
//     {error:'agent_token_invalid'}; an agent's reaction is never recorded as the operator's);
//   - no agent header -> the principal's username (in the default untrusted mode, always the configured operator).
// There is no trust
// based on loopback or on a missing login header (Tailscale Serve traffic arrives without one too). The token
// header's value is never logged (lib/access-log.ts withholds any header named *token*).
import type { Request } from 'express';
import { principal } from './principal.js';
import { agentTokenMatches } from './agent-react-token.js';
import { isSafeAgentId } from './agentPaths.js';
import type { Reactor } from './reactions-store.js';

function header(req: Request, name: string): string {
  const v = req.headers[name];
  return (Array.isArray(v) ? v[0] : v || '').toString().trim();
}

/** Returned when an agent header is present without a valid token. */
export const AGENT_TOKEN_INVALID = 'agent_token_invalid' as const;

export function reactorOf(req: Request): Reactor | typeof AGENT_TOKEN_INVALID | null {
  const p = principal(req);
  if (!p) return null;
  const agent = header(req, 'x-orchestra-agent');
  const token = header(req, 'x-orchestra-agent-token');
  if (agent) {
    if (token && isSafeAgentId(agent) && agentTokenMatches(token)) return { kind: 'agent', id: agent };
    return AGENT_TOKEN_INVALID;
  }
  return { kind: 'user', id: p.username };
}
