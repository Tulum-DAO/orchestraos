/**
 * Pure hydration of a replayed thread into the flat timeline (Phase 1).
 * Type-only imports so it's unit-testable under `node --experimental-strip-types`
 * (no runtime config / import.meta.env). Kept separate from threads.ts (which
 * has the fetchers + config deps) for exactly that reason.
 *
 * Reconstructs the SAME TurnItem[] the streaming path produces, INCLUDING
 * persisted tool-call/gate cards (the operator ruling #3: gate lifecycle replays).
 */
import type { ThreadDetail, ThreadToolCall } from './contract';
import type {
  TimelineState,
  TurnItem,
  ToolCallItem,
  ToolCallPhase,
} from './timeline';
import type {
  UserMessageEvent,
  AssistantMessageEvent,
} from '../claude-code-protocol';

/** Derive the UI phase of a persisted tool call from its terminal fields. */
function toolCallPhase(tc: ThreadToolCall): ToolCallPhase {
  if (tc.skippedWhy) return 'skipped';
  if (tc.results !== undefined || tc.ok !== undefined) return 'done';
  if (tc.decision) return 'decided';
  return 'proposed';
}

let seq = 0;
const key = (p: string) => `${p}-h${seq++}`;

/**
 * Pure: reconstruct a TimelineState from a replayed thread. Turns render in
 * order; an assistant turn's persisted `toolCalls` become tool_call items right
 * after that turn's text (mirrors streaming order). No active assistant segment
 * afterwards — a fresh send opens a new one.
 */
export function hydrateTimeline(detail: ThreadDetail): TimelineState {
  const items: TurnItem[] = [];
  for (const turn of detail.turns) {
    if (turn.role === 'user') {
      const u: UserMessageEvent = { type: 'user_message', content: turn.text, id: key('u') };
      items.push(u);
    } else {
      const a: AssistantMessageEvent = { type: 'assistant_message', content: turn.text, id: key('a') };
      items.push(a);
      for (const tc of turn.toolCalls ?? []) {
        const item: ToolCallItem = {
          type: 'tool_call',
          id: `tc-${tc.toolCallId}`,
          toolCallId: tc.toolCallId,
          tool: tc.tool,
          args: tc.args,
          taint: tc.taint,
          quadrant: tc.quadrant,
          rationale: tc.rationale,
          phase: toolCallPhase(tc),
          decision: tc.decision,
          mode: tc.mode,
          reason: tc.reason,
          overridable: tc.overridable,
          ok: tc.ok,
          results: tc.results,
          skippedWhy: tc.skippedWhy,
        };
        items.push(item);
      }
    }
  }
  return { items, activeAssistantId: null };
}
