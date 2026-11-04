/**
 * agentOverrides.ts — Settings › Agents "N of M use their own": for each install default row, which agents keep their own
 * value instead (state/seat-settings.json seats.<id>.<field>, the same file and validity rule lib/seatSettings.ts
 * readSeatSettings uses, so "uses its own" here is exactly `source: 'seat'` there).
 *
 *   M (total)  the registered agents these settings apply to: a seat on a runtime our rotation drives (seatRotates;
 *              never Arturo). An agent on another runtime has no compaction settings, so it is in neither number.
 *   N          those of them with a valid value of their own for the field (even one equal to the default: it is
 *              pinned, and a change of the default does not reach it).
 *   A seat with a value in the file that is no longer registered is not counted.
 *
 * Default model has no count: an agent's model is chosen once, when it is made (spawnEnv, routes/agents-new.ts), and is
 * then the agent's own CLI setting, read live through the gateway one agent at a time. No agent "follows" the default
 * afterwards, so there is no honest "uses its own" to count.
 */
import { readSeatOverrides, seatRotates, type SettingField } from './seatSettings.js';

export const OVERRIDE_FIELDS: Readonly<Record<string, SettingField>> = {
  'agents.autoCompact': 'auto_compact',
  'agents.compactAt': 'compact_at',
  'agents.showCompaction': 'show_compaction',
};

export const NOT_COUNTED: Readonly<Record<string, string>> = {
  'agents.defaultModel': 'An agent keeps the model it was made with; none follows the default afterwards, so there is nothing to count.',
};

export interface AgentOverride { id: string; value: boolean | number }
export interface OverridesReport { total: number; agents: string[]; rows: Record<string, AgentOverride[]>; notCounted: Record<string, string> }

type Registry = { agents?: Record<string, { runtime?: unknown }> } | null;

export function agentOverrides(dataDir: string, registry: Registry): OverridesReport {
  const agents = Object.keys(registry?.agents ?? {}).filter((id) => seatRotates(id, registry)).sort();
  const own = readSeatOverrides(dataDir);
  const rows: Record<string, AgentOverride[]> = {};
  for (const [key, field] of Object.entries(OVERRIDE_FIELDS)) {
    rows[key] = agents.filter((id) => own[id] && field in own[id]).map((id) => ({ id, value: own[id][field] as boolean | number }));
  }
  return { total: agents.length, agents, rows, notCounted: { ...NOT_COUNTED } };
}
