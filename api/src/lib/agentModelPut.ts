/**
 * agentModelPut.ts — the body PUT /api/agents/:id/model sends on to the gateway's PUT /agent-model: the model, and an
 * effort level when one was picked (scripts/agent_effort.py; the gateway checks it against the agent's runtime).
 * No effort means the agent's effort is left as it is.
 */
import { execFileSync } from 'node:child_process';
import { codePath } from './codeRoot.js';

const LEVEL_IDS = ['low', 'medium', 'high', 'xhigh', 'max'];

/** How long PUT waits for the gateway: a Claude switch types /model and then /effort (the agent's kept level is set
 *  again on the new model), each up to 10 s for the CLI's line + 3 s settle + 5 s recheck. The dashboard proxy waits
 *  longer (dashboard-proxy.js MODEL_PUT_PROXY_TIMEOUT_MS), so the picker hears the answer, not a dropped connection. */
export const MODEL_PUT_TIMEOUT_MS = 55000;

/** scripts/agent_effort.py in this checkout (lib/codeRoot.ts: the same root spawn-agent.sh is run from). */
const EFFORT_SCRIPT = codePath('scripts', 'agent_effort.py');

type Run = (cmd: string, args: string[]) => void;
const runPython: Run = (cmd, args) => { execFileSync(cmd, args, { timeout: 10000, stdio: 'ignore' }); };

/** Forget the effort kept for an agent name (state/agent-efforts.json), before a new agent is made under it: a reused
 *  name starts on the default, never on an old agent's level. Done by scripts/agent_effort.py forget, so the write
 *  takes the same lock (agent-efforts.json.lock) and lineage rule as every other writer. Throws when it fails. */
export function forgetAgentEffort(dataDir: string, name: string, run: Run = runPython): void {
  run('python3', [EFFORT_SCRIPT, 'forget', name, '--orchestra-dir', dataDir]);
}

export function parseModelPut(body: unknown): { model: string; effort?: string } | { error: string } {
  const b = (body && typeof body === 'object' ? body : {}) as { model?: unknown; effort?: unknown };
  if (typeof b.model !== 'string' || !b.model.trim()) return { error: 'model required' };
  const model = b.model.trim();
  if (b.effort === undefined || b.effort === null) return { model };
  if (typeof b.effort !== 'string' || !LEVEL_IDS.includes(b.effort)) {
    return { error: `effort must be a level id (${LEVEL_IDS.join(', ')})` };
  }
  return { model, effort: b.effort };
}
