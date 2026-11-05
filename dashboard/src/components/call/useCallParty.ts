/** Who a live call is with, as every call surface names it (the bar, the chip, the sidebar pill): the agent when its
 *  calls reach it, Arturo when the call went through him (lib/agentCallsOn.ts). */
import { ARTURO_ID, useAgentRecord } from '../avatar/agentState';
import { callPartyId } from '../../lib/agentCallsOn';
import type { LiveCall } from '../../stores/callStore';

export function useCallParty(call: LiveCall | null) {
  const id = call ? callPartyId(call, ARTURO_ID) : '';
  const rec = useAgentRecord(id);
  return { id, rec, name: rec.name || id };
}
