/**
 * agentDefaults.ts — the model and effort a NEW agent starts on, per runtime (Settings › Agents "Default model"):
 *   <data dir>/state/agent-defaults.json   {"models": {"claude": "…", "codex": "…"}, "efforts": {"codex": "high"}}
 * spawnEnv (routes/agents-new.ts) reads it at spawn; a runtime with no model uses BUILTIN_MODELS, one with no effort
 * passes none (the CLI's own default). The effort goes to spawn-agent.sh as AGENT_EFFORT, which keeps it for the agent
 * (scripts/agent_effort.py), so it is the agent's own from then on.
 */
import fs from 'fs';
import path from 'path';

export const BUILTIN_MODELS: Record<string, string> = {
  claude: 'claude-opus-5-5[1m]',
  gemini: 'gemini-3.1-pro',
  codex: 'gpt-5.6-terra',
  grok: 'grok-4.7',
};
export const MODEL_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@[\]-]{0,99}$/;
/** The effort levels each runtime's CLI takes (scripts/agent_effort.py LEVELS; Gemini and Grok have none). */
export const EFFORT_LEVELS: Readonly<Record<string, readonly string[]>> = {
  claude: ['low', 'medium', 'high', 'xhigh', 'max'],
  codex: ['low', 'medium', 'high', 'xhigh'],
};
const defaultsFile = (dataDir: string) => path.join(dataDir, 'state', 'agent-defaults.json');

function readFile(dataDir: string): { models?: unknown; efforts?: unknown } {
  try {
    const d = JSON.parse(fs.readFileSync(defaultsFile(dataDir), 'utf-8'));
    return d && typeof d === 'object' ? d : {};
  } catch { return {}; }
}

function writeFile(dataDir: string, data: { models: Record<string, string>; efforts: Record<string, string> }): void {
  const f = defaultsFile(dataDir);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.${process.pid}.tmp`;
  const out = Object.keys(data.efforts).length ? data : { models: data.models };
  fs.writeFileSync(tmp, JSON.stringify(out, null, 2) + '\n');
  fs.renameSync(tmp, f);
}

export function readDefaultModels(dataDir: string): Record<string, string> {
  const m = readFile(dataDir).models;
  if (!m || typeof m !== 'object') return {};
  return Object.fromEntries(Object.entries(m).filter(([rt, id]) => rt in BUILTIN_MODELS && typeof id === 'string' && MODEL_ID_RE.test(id))) as Record<string, string>;
}

export function readDefaultEfforts(dataDir: string): Record<string, string> {
  const m = readFile(dataDir).efforts;
  if (!m || typeof m !== 'object') return {};
  return Object.fromEntries(Object.entries(m).filter(([rt, lv]) => typeof lv === 'string' && (EFFORT_LEVELS[rt] ?? []).includes(lv))) as Record<string, string>;
}

export function writeDefaultModel(dataDir: string, runtime: string, model: string): void {
  if (!(runtime in BUILTIN_MODELS)) throw new Error(`unknown runtime: ${runtime}`);
  if (!MODEL_ID_RE.test(model)) throw new Error('not a model id');
  writeFile(dataDir, { models: { ...readDefaultModels(dataDir), [runtime]: model }, efforts: readDefaultEfforts(dataDir) });
}

/** One runtime's default effort; null takes it out (new agents start on the CLI's default). */
export function writeDefaultEffort(dataDir: string, runtime: string, effort: string | null): void {
  const levels = EFFORT_LEVELS[runtime];
  if (!levels) throw new Error(`${runtime} has no effort setting`);
  if (effort !== null && !levels.includes(effort)) throw new Error(`not a ${runtime} effort level: ${effort}`);
  const efforts = { ...readDefaultEfforts(dataDir) };
  if (effort === null) delete efforts[runtime]; else efforts[runtime] = effort;
  writeFile(dataDir, { models: readDefaultModels(dataDir), efforts });
}
