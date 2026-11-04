/**
 * settingsPreview.ts — the tiny fixed conversation the Settings preview draws (General under Text size, and Display):
 * the person's question, the agent's run of two commands, and its reply, each with its time. It goes through the same
 * steps as an agent's chat (lib/transcript buildRenderList, lib/threadKit/results markResults, lib/toolGroups
 * groupToolRuns, lib/decisions interleave, lib/threadKit/view agentRows), so the preview's ThreadView draws exactly what
 * the real chat would in the chosen Chat style. Pure and fixed: no fetch, no stream, no send.
 */
import { buildRenderList, type ChatItem } from './transcript';
import { markResults } from './threadKit/results';
import { groupToolRuns } from './toolGroups';
import { interleave } from './decisions';
import { agentRows, type ThreadRow } from './threadKit/view';

/** The id the preview's rows are built for: no agent has it, and nothing is ever read or sent for it. */
export const PREVIEW_AGENT = 'settings-preview';

export type PreviewSkin = 'bubbles' | 'modern';

/** Settings › General › Chat style (the server row general.defaultChatStyle) as the skin the preview draws; anything
 *  but Modern (not loaded yet, an unknown value) is Bubbles, the chat's own default. */
export function previewSkin(style: unknown): PreviewSkin {
  return style === 'modern' ? 'modern' : 'bubbles';
}

/** A fixed time (minutes after the start), shown like the chat's own: in the install's time zone (Settings › Time zone). */
const at = (min: number) => new Date(Date.UTC(2026, 0, 1, 15, min)).toISOString();

/** The conversation: a question, two commands (so Show agent steps folds or opens the run), the answer. */
export function previewItems(): ChatItem[] {
  return [
    { kind: 'text', role: 'user', text: 'Is the deploy done?', uuid: 'pv-u', ts: at(0) },
    { kind: 'tool_use', role: 'assistant', tool: 'Bash', input: { command: 'npm test' }, summary: 'npm test', id: 'pv-t1', uuid: 'pv-a1', ts: at(1) },
    { kind: 'tool_result', role: 'user', text: 'All 412 tests passed', tool_use_id: 'pv-t1', is_error: false, uuid: 'pv-r1', ts: at(1) },
    { kind: 'tool_use', role: 'assistant', tool: 'Bash', input: { command: 'netlify status' }, summary: 'netlify status', id: 'pv-t2', uuid: 'pv-a2', ts: at(2) },
    { kind: 'tool_result', role: 'user', text: 'Deploy live', tool_use_id: 'pv-t2', is_error: false, uuid: 'pv-r2', ts: at(2) },
    { kind: 'text', role: 'assistant', text: 'Yes, it finished and every check is green.', uuid: 'pv-a3', ts: at(2) },
  ];
}

/** The preview's rows, as the agent chat builds them for this skin (Modern: plain messages carry the message it draws). */
export function previewRows(skin: PreviewSkin): ThreadRow[] {
  const nodes = groupToolRuns(markResults(buildRenderList(previewItems()), PREVIEW_AGENT));
  return agentRows(interleave(nodes, []), [], 'idle', { modern: skin === 'modern' });
}
