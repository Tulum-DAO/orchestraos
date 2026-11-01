/**
 * agentCallsOn.ts — does a call from this agent's page reach the agent itself, or (still) Arturo?
 *
 * GET /api/agents/:id/settings carries `agent_calls: boolean`. Only `true` means the call goes to the agent: its round
 * button reads "Call <agent>", and the call bar, the page-top chip and the sidebar pill name the agent. Anything else
 * (false, the field missing on an older API, the settings not readable) means the call is Arturo's, as before agent
 * calls: "Call Arturo", and every surface says Arturo.
 */
import { create } from 'zustand';
import { useEffect } from 'react';

/** The settings answer's flag: true only when it says so. */
export function agentCallsFrom(json: unknown): boolean {
  const o = json as Record<string, unknown> | null;
  if (!o || typeof o !== 'object') return false;
  const s = (o.settings && typeof o.settings === 'object' ? o.settings : o) as Record<string, unknown>;
  return s.agent_calls === true;
}

/** Read it for one agent; false when the server does not say true (or cannot be reached). */
export async function fetchAgentCallsOn(agentId: string, fetchFn: typeof fetch = fetch): Promise<boolean> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/settings`);
    if (!r.ok) return false;
    return agentCallsFrom(await r.json().catch(() => null));
  } catch {
    return false;
  }
}

/** Who a call started from this page is with: the agent when its calls are on, else Arturo. */
export function calleeName(agentCalls: boolean, agentName: string, arturoName: string): string {
  return agentCalls ? agentName : arturoName;
}

/** Who a live call is with, for every surface that names it: Arturo's id when it went through him. */
export function callPartyId(call: { agentId: string; party?: 'agent' | 'arturo' }, arturoId: string): string {
  return call.party === 'agent' ? call.agentId : arturoId;
}

interface Store { on: Record<string, boolean | undefined>; set: (id: string, v: boolean) => void }
export const useAgentCallsStore = create<Store>((set) => ({
  on: {},
  set: (id, v) => set((s) => (s.on[id] === v ? s : { on: { ...s.on, [id]: v } })),
}));

/** The flag for this agent, read when its page opens; false until the answer says true. */
export function useAgentCallsOn(agentId: string): boolean {
  const on = useAgentCallsStore((s) => s.on[agentId]);
  useEffect(() => {
    if (!agentId) return;
    let alive = true;
    void fetchAgentCallsOn(agentId).then((v) => { if (alive) useAgentCallsStore.getState().set(agentId, v); });
    return () => { alive = false; };
  }, [agentId]);
  return on === true;
}
