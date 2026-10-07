/**
 * The ONE guard for filesystem paths built from an agent id (gm msg_c146a84e, 2026-10-07).
 *
 * The sweep that found POST /api/questionnaires/:id/submit found three more routes building a
 * path from a request-supplied agent id. Express decodes %2F in route params, so
 * /api/agents/..%2F..%2Fx/... walked out of the intended directory:
 *   - PUT /api/agents/:id/prompt   wrote ARBITRARY .md CONTENT anywhere (for an unregistered id
 *     the path was `prompts/${id}.md`): fleet-wide prompt injection. GET read any .md the same way.
 *   - POST /api/agents/:id/task    mkdir + JSON write under queue/inbox/<id>
 *   - POST /api/inspect-feedback   mkdir + JSON write under queue/inbox/<body.agentId>
 * Rule for all of them: a strict id, the id must be a REGISTERED agent (unknown -> 404, nothing
 * created), and the resolved path must stay inside its base directory.
 * Measured 2026-10-07: all 330 registered ids match SAFE_AGENT_ID (letters, digits, '-'), and all
 * 54 registry system_prompt values are `prompts/*.md`.
 */
import { resolve, sep } from 'path';

export const SAFE_AGENT_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

export class UnsafeAgentPath extends Error {}

/** `rel` resolved under `base`; throws unless the result is strictly INSIDE base. */
export function containedPath(base: string, rel: string): string {
  const root = resolve(base);
  const full = resolve(root, rel);
  if (!full.startsWith(root + sep)) throw new UnsafeAgentPath('path escapes ' + root);
  return full;
}

export function isSafeAgentId(id: unknown): id is string {
  return typeof id === 'string' && SAFE_AGENT_ID.test(id);
}

/** A registered agent's entry, or null. Unknown and malformed ids are both null. */
export function registeredAgent(registry: any, id: unknown): Record<string, any> | null {
  if (!isSafeAgentId(id)) return null;
  const a = registry?.agents?.[id];
  return a && typeof a === 'object' ? a : null;
}

/** Absolute prompt path for a REGISTERED agent: inside <orch>/prompts and ending in .md. */
export function promptPathFor(orchDir: string, id: string, agent: { system_prompt?: unknown }): string {
  const rel = typeof agent.system_prompt === 'string' && agent.system_prompt ? agent.system_prompt : `prompts/${id}.md`;
  if (!rel.endsWith('.md')) throw new UnsafeAgentPath('prompt must be a .md file');
  const promptsDir = resolve(orchDir, 'prompts');
  const full = containedPath(orchDir, rel);
  if (!full.startsWith(promptsDir + sep)) throw new UnsafeAgentPath('prompt must live under prompts/');
  return full;
}

/** <orch>/queue/inbox/<id>, for a SAFE id, contained. */
export function inboxDirFor(orchDir: string, id: string): string {
  if (!isSafeAgentId(id)) throw new UnsafeAgentPath('bad agent id');
  return containedPath(resolve(orchDir, 'queue', 'inbox'), id);
}
