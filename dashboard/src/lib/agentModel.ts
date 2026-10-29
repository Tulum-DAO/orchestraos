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
  models: { id: string; title?: string; label?: string; available?: boolean; reason?: string }[];
  pending?: { model?: string } | null;
}

import type { ProviderModel } from './modelSelectorFilter';

export interface SwitchNotice { tone: 'ok' | 'wait' | 'error'; text: string }

/** The parts of the gateway's PUT answer the picker reads. */
export interface SwitchBody {
  ok?: boolean; error?: string; changed?: boolean; cancelled?: boolean; switching?: string; warning?: string;
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

export async function switchAgentModel(agentId: string, model: string): Promise<{ status: number; body: SwitchBody }> {
  try {
    const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  } catch {
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
export function switchableModels(info: AgentModelInfo | null): ProviderModel[] {
  return (info?.models ?? []).filter((m) => m.available !== false).map((m) => ({
    id: m.id,
    label: m.title || m.label || m.id,
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
    return { tone: 'ok', text: `Switched to ${label}.` };
  }
  if (status === 202 && body?.ok) return { tone: 'wait', text: `${label} will apply when the agent finishes its current turn.` };
  if (status === 0) return { tone: 'error', text: "Couldn't reach OrchestraOS. Nothing was switched." };
  if (status === 502 && body?.error === 'gateway unreachable') {
    return { tone: 'error', text: 'No answer in time. Check the model again in a moment: the switch may still go through.' };
  }
  const why = typeof body?.error === 'string' && body.error ? body.error : `error ${status}`;
  return { tone: 'error', text: `Couldn't switch to ${label}: ${why}.` };
}
