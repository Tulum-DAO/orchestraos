/**
 * agentModel.ts — switching a running agent's model from its page.
 *
 * The gateway does the switch (scripts/agent_model.py, through /api/agents/:id/model): live where the
 * agent's CLI allows it, at its next rotation where it does not, and a busy agent's switch waits for its
 * turn to end. This file turns its answers into what the picker shows, kept here so it can be tested
 * without a browser.
 */

export interface AgentModelInfo {
  runtime: string | null;
  model: string | null;
  /** 'live' | 'next_rotation', or null when this agent cannot switch (see `models[].reason`). */
  how: string | null;
  allowed: string[];
  /** The gateway names each model in `title` (the Arturo engine picker's shape); `label` is accepted too. */
  models: { id: string; title?: string; label?: string; subtitle?: string; available?: boolean; reason?: string }[];
  pending?: { model?: string; effort?: string; since?: string } | null;
  /** How the last switch kept for a turn end closed (scripts/agent_model_pending.py `last`): read by lib/liveSwitch.ts. */
  last_switch?: { model?: string; effort?: string; at?: string; outcome?: string; reason?: string | null } | null;
  /** The effort level kept for this agent (null: the CLI's default), its runtime's levels, and 'live' when a picked
   *  level is applied (scripts/agent_effort.py); null where it cannot be (no levels, a headless seat whose supervisor
   *  predates effort or is not running). Absent from an older gateway: then no effort control. */
  effort?: string | null;
  effort_levels?: string[];
  effort_how?: string | null;
}

import type { ProviderModel } from './modelSelectorFilter';
import { EFFORT, effortLevels, type EffortLevel } from './modelEffort';

export interface SwitchNotice { tone: 'ok' | 'wait' | 'error'; text: string }

/** The parts of the gateway's PUT answer the picker reads. */
export interface SwitchBody {
  ok?: boolean; error?: string; changed?: boolean; cancelled?: boolean; switching?: string; warning?: string;
  status?: string; applies?: string; effort?: string; effort_switching?: string; model_switched?: boolean;
  effort_warning?: string; effort_not_set?: string;
  /** A 202: when the gateway recorded the switch kept for the turn end (lib/liveSwitch.ts, turn end). */
  pending_since?: string;
}

export async function fetchAgentModel(agentId: string): Promise<AgentModelInfo | null> {
  try {
    const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}/model`);
    if (!r.ok) return null;
    const body = await r.json();
    return body && body.ok !== false ? (body as AgentModelInfo) : null;
  } catch {
    return null;
  }
}

/** How long a switch waits for its answer: longer than the API's own wait on the gateway (MODEL_PUT_TIMEOUT_MS, 55 s)
 *  and the dashboard proxy's (60 s), so a slow switch is heard, yet a request that never answers frees the agent's
 *  queue (lib/liveSwitch.ts) instead of holding every later pick. */
export const SWITCH_TIMEOUT_MS = 70_000;

/** `effort`: a level id to apply too (the model may be the one it is on: an effort-only change). No answer within
 *  `timeoutMs`: status 0 with error 'timeout' (the switch may still go through). */
export async function switchAgentModel(agentId: string, model: string, effort?: string, timeoutMs = SWITCH_TIMEOUT_MS):
  Promise<{ status: number; body: SwitchBody }> {
  try {
    const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(effort ? { model, effort } : { model }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  } catch (e) {
    const name = (e as { name?: string } | null)?.name;
    if (name === 'TimeoutError' || name === 'AbortError') return { status: 0, body: { error: 'timeout' } };
    return { status: 0, body: {} };
  }
}

/** The label for a model id, from the agent's own list; the id itself when it has none. */
export function modelLabel(info: AgentModelInfo | null, id: string | null | undefined): string | null {
  if (!id) return null;
  const m = info?.models.find((x) => x.id === id);
  return m?.title || m?.label || id;
}

/** The models this agent can switch to, as picker rows. Capabilities are not part of the gateway's answer, so
 *  none are claimed. */
export function switchableModels(info: AgentModelInfo | null): (ProviderModel & { subtitle?: string })[] {
  return (info?.models ?? []).filter((m) => m.available !== false).map((m) => ({
    id: m.id,
    label: m.title || m.label || m.id,
    // the context size ("200K context"), which the model picker shows beside the name
    ...(m.subtitle ? { subtitle: m.subtitle } : {}),
    capabilities: { text: true, image: false, audio: false, video: false, context_window: null },
  }));
}

/** Why a pick cannot be made for this agent, before asking the gateway; null when it can. An agent runs on
 *  one CLI, so a model from another provider means a new agent, not a switch. */
export function pickRefusal(info: AgentModelInfo | null, pick: { providerId: string; modelLabel: string }): string | null {
  if (!info) return "This agent's model can't be read right now, so it can't be switched.";
  if (info.runtime && pick.providerId !== info.runtime) {
    return `This agent runs on ${providerName(info.runtime)}. To use ${pick.modelLabel}, start a new agent on ${providerName(pick.providerId)}.`;
  }
  if (!info.how) {
    const reason = info.models.find((m) => m.reason)?.reason;
    return `This agent can't switch models${reason ? `: ${reason}` : ''}.`;
  }
  return null;
}

const PROVIDER_NAMES: Record<string, string> = { claude: 'Claude', codex: 'Codex', gemini: 'Gemini', grok: 'Grok' };

function providerName(id: string): string {
  return PROVIDER_NAMES[id] ?? id;
}

/** What the gateway's answer to a switch means, in plain words. */
export function switchNotice(status: number, body: SwitchBody, label: string): SwitchNotice {
  if (status === 200 && body?.ok) {
    if (body.changed === false) {
      return { tone: 'ok', text: body.cancelled ? `Kept ${label}; the waiting switch was cancelled.` : `Already on ${label}.` };
    }
    if (body.switching === 'next_rotation') return { tone: 'wait', text: `${label} will be used from this agent's next rotation.` };
    if (body.warning) return { tone: 'wait', text: `Switched to ${label}, but its next restart will be on the old model.` };
    if (body.effort_warning && body.effort_not_set) {
      // A Claude switch sets the agent's kept level again on the new model; this time it did not take.
      const lv = EFFORT.claude.levels.find((l) => l.id === body.effort_not_set)?.label ?? body.effort_not_set;
      return { tone: 'wait', text: `Switched to ${label}, but its ${lv} effort could not be set again.` };
    }
    return { tone: 'ok', text: `Switched to ${label}.` };
  }
  if (status === 202 && body?.ok) return { tone: 'wait', text: `${label} will apply when the agent finishes its current turn.` };
  if (status === 0 && body?.error === 'timeout') {
    return { tone: 'error', text: 'No answer in time. Check the model again in a moment: the switch may still go through.' };
  }
  if (status === 0) return { tone: 'error', text: "Couldn't reach OrchestraOS. Nothing was switched." };
  // The API could not hear the gateway, or the dashboard proxy could not hear the API: either way unknown.
  if (status === 502 && (body?.error === 'gateway unreachable' || body?.error === 'upstream unreachable')) {
    return { tone: 'error', text: 'No answer in time. Check the model again in a moment: the switch may still go through.' };
  }
  const why = typeof body?.error === 'string' && body.error ? body.error : `error ${status}`;
  return { tone: 'error', text: `Couldn't switch to ${label}: ${why}.` };
}

/** When a pick takes effect, said under the picker only when it is unusual. A live switch (Claude, Codex) lands before
 *  the next message, or as soon as a busy agent finishes its turn: the usual timing, so nothing is said (the notice
 *  after a pick says what happened). One that waits for the agent's next rotation (scripts/agent_model.py) says so.
 *  Null otherwise, and when this agent can't switch. */
export function applyNote(info: AgentModelInfo | null): string | null {
  return info?.how === 'next_rotation' ? "Applies from this agent's next rotation" : null;
}

/** The effort levels to offer for this agent: its runtime's own (lib/modelEffort.ts), only where the gateway says a
 *  picked level reaches it (effort_how), and of those only the ones the gateway lists for it (effort_levels) when it
 *  lists them. Null: no effort control for this agent (none left, or fewer than two to choose between). */
export function agentEffortLevels(info: AgentModelInfo | null): EffortLevel[] | null {
  if (!info?.runtime || !info.how || !info.effort_how) return null;
  const all = effortLevels(info.runtime, 'agent');
  if (!all || !Array.isArray(info.effort_levels) || !info.effort_levels.length) return all;
  const own = all.filter((l) => info.effort_levels!.includes(l.id));
  return own.length >= 2 ? own : null;
}

/** What the gateway's answer to an effort pick means, in plain words. `label` is the level's name ("High"). */
export function effortNotice(status: number, body: SwitchBody, label: string): SwitchNotice {
  if (status === 200 && body?.ok) {
    if (body.changed === false) return { tone: 'ok', text: `Already on ${label} effort.` };
    if (body.effort_switching === 'next_start') return { tone: 'ok', text: `${label} effort applies when the agent next restarts.` };
    return { tone: 'ok', text: `Effort set to ${label}.` };
  }
  if (status === 202 && body?.ok) return { tone: 'wait', text: `${label} effort will apply when the agent finishes its current turn.` };
  if (status === 0 && body?.error === 'timeout') {
    return { tone: 'error', text: 'No answer in time. Check the effort again in a moment: the change may still go through.' };
  }
  if (status === 0) return { tone: 'error', text: "Couldn't reach OrchestraOS. The effort wasn't changed." };
  const why = typeof body?.error === 'string' && body.error ? body.error : `error ${status}`;
  return { tone: 'error', text: `Couldn't set ${label} effort: ${why}.` };
}
