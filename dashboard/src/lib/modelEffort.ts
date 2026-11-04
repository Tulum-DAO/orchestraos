/**
 * modelEffort.ts — how hard a model thinks ("effort"), per runtime: the levels each runtime's CLI really has, and
 * whether OrchestraOS applies a chosen level. The picker's Effort control shows a runtime's own levels, and only where
 * a choice is actually applied; nowhere else. scripts/agent_effort.py holds the same levels server-side.
 *
 * What each runtime has, and how a level reaches it (checked 2026-11-03):
 *   - Claude Code (2.1.295): `--effort <level>`, levels low, medium, high, xhigh, max. A running seat gets
 *     `/effort <level>` typed into its idle pane (a busy one when its turn ends), confirmed by the CLI's own "Set
 *     effort level to" line; spawn-agent.sh passes `--effort` at every spawn and rotation, so it stays.
 *   - Codex: reasoning effort low, medium, high, xhigh. A running seat's model picker picks the level
 *     (scripts/agent_model_codex.py); spawns pass `-c model_reasoning_effort=<level>`.
 *   - Gemini: the level is part of the model id ("Gemini 3.8 Flash (High)" is its own model), so there is no separate
 *     control: picking the model picks the level.
 *   - Grok: no effort setting OrchestraOS drives.
 * Defaults (Settings › Agents): agents.defaultEffort, passed to a new agent's spawn as AGENT_EFFORT.
 * A headless seat (scripts/seat_supervisor.py) gets the level through its supervisor: the CLI's own apply_flag_settings
 * control request between turns (read back; a busy one at the end of its turn), and --effort at every start of its
 * CLI, so effort_how is 'live' there too. Only a supervisor started before effort support (until it restarts), or one
 * not running, answers effort_how: null, and the control stays hidden.
 */

export interface EffortLevel { id: string; label: string }

export interface RuntimeEffort {
  levels: EffortLevel[];
  /** Whether a chosen level reaches the agent today (per agent / as a default). */
  applies: { agent: boolean; defaults: boolean };
  /** When a chosen level takes effect, said once in code. */
  note: string;
}

const L = (id: string, label: string): EffortLevel => ({ id, label });

export const EFFORT: Record<string, RuntimeEffort> = {
  claude: {
    levels: [L('low', 'Low'), L('medium', 'Medium'), L('high', 'High'), L('xhigh', 'Extra high'), L('max', 'Max')],
    applies: { agent: true, defaults: true },
    note: 'A running agent gets /effort <level> before your next message (at the end of its turn when busy); every spawn and rotation passes --effort.',
  },
  codex: {
    levels: [L('low', 'Low'), L('medium', 'Medium'), L('high', 'High'), L('xhigh', 'Extra high')],
    applies: { agent: true, defaults: true },
    note: "A running agent's model picker picks the level before your next message (at the end of its turn when busy); every spawn passes -c model_reasoning_effort.",
  },
};

/** The levels to offer for this runtime in this place, or null: no control (no levels, or no way to apply one). */
export function effortLevels(runtime: string | null | undefined, where: 'agent' | 'defaults',
  table: Record<string, RuntimeEffort> = EFFORT): EffortLevel[] | null {
  const e = runtime ? table[runtime] : undefined;
  if (!e || !e.levels.length || !e.applies[where]) return null;
  return e.levels;
}

/** The pill's effort word for a level id ("medium" → "Medium"); null when the runtime has no such level. */
export function effortLabel(runtime: string | null | undefined, level: string | null | undefined,
  table: Record<string, RuntimeEffort> = EFFORT): string | null {
  if (!runtime || !level) return null;
  return table[runtime]?.levels.find((l) => l.id === level)?.label ?? null;
}

/** The control's choices: the levels, with "CLI default" (id '') first when a chosen level can be taken out again. */
export function effortChoices(levels: EffortLevel[], clearable: boolean): EffortLevel[] {
  return clearable ? [L('', 'CLI default'), ...levels] : levels;
}

/** A choice's id back to a level: '' (CLI default) is null. */
export function effortFromChoice(id: string): string | null {
  return id === '' ? null : id;
}
