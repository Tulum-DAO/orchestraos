/**
 * agentDefaults.ts — the model a NEW agent starts on, per runtime (Settings › Agents "Default model for new agents"):
 *   <data dir>/state/agent-defaults.json   {"models": {"claude": "…", "codex": "…"}}
 * spawnEnv (routes/agents-new.ts) reads it at spawn; a runtime with none uses BUILTIN_MODELS.
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
const defaultsFile = (dataDir: string) => path.join(dataDir, 'state', 'agent-defaults.json');

export function readDefaultModels(dataDir: string): Record<string, string> {
  try {
    const m = JSON.parse(fs.readFileSync(defaultsFile(dataDir), 'utf-8'))?.models;
    if (!m || typeof m !== 'object') return {};
    return Object.fromEntries(Object.entries(m).filter(([rt, id]) => rt in BUILTIN_MODELS && typeof id === 'string' && MODEL_ID_RE.test(id))) as Record<string, string>;
  } catch { return {}; }
}

export function writeDefaultModel(dataDir: string, runtime: string, model: string): void {
  if (!(runtime in BUILTIN_MODELS)) throw new Error(`unknown runtime: ${runtime}`);
  if (!MODEL_ID_RE.test(model)) throw new Error('not a model id');
  const f = defaultsFile(dataDir);
  const models = { ...readDefaultModels(dataDir), [runtime]: model };
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ models }, null, 2) + '\n');
  fs.renameSync(tmp, f);
}
