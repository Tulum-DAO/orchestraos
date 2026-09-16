/**
 * Converse turn timeline (B2, Decision 1A — flat unified timeline).
 *
 * A conversation is a flat, ordered list of TurnItems rendered in true
 * arrival order: user text, assistant text segments, and tool calls
 * interleaved exactly as they streamed. Tool-call lifecycle events
 * (proposed → decision → result | skipped) are merged into a single
 * ToolCallItem keyed by toolCallId.
 *
 * This module is a PURE reducer (no React) so the merge logic is unit
 * testable. useConverse holds `TurnItem[]` in state and folds each stream
 * event through `applyStreamEvent`.
 */
import type {
  UserMessageEvent,
  AssistantMessageEvent,
} from '../claude-code-protocol';
import type {
  ConverseStreamEvent,
  ToolTaint,
  ToolQuadrant,
} from './contract';

/** Lifecycle phase of a tool call as the UI knows it. */
export type ToolCallPhase = 'proposed' | 'decided' | 'done' | 'skipped';

/** A single tool call, accreted from its lifecycle events (by toolCallId). */
export interface ToolCallItem {
  type: 'tool_call';
  id: string; // React key — `tc-${toolCallId}`
  toolCallId: string;
  tool: string;
  args: Record<string, unknown>;
  taint: ToolTaint;
  quadrant: ToolQuadrant;
  rationale?: string;
  phase: ToolCallPhase;
  // From tool_call_decision:
  decision?: 'allow' | 'block' | 'require_human';
  mode?: 'shadow' | 'live';
  reason?: string;
  overridable?: boolean;
  // From tool_call_result:
  ok?: boolean;
  results?: string[];
  expandable?: string;
  // From tool_call_skipped:
  skippedWhy?:
    | 'shadow'
    | 'blocked_lethal'
    | 'not_graduated'
    | 'human_rejected'
    | 'human_timeout';
}

/** Anything that can appear in the flat timeline. */
export type TurnItem = UserMessageEvent | AssistantMessageEvent | ToolCallItem;

export interface TimelineState {
  items: TurnItem[];
  /** id of the assistant-message segment currently accepting deltas, if any. */
  activeAssistantId: string | null;
}

export const emptyTimeline = (): TimelineState => ({
  items: [],
  activeAssistantId: null,
});

let seq = 0;
const genId = (p: string) => `${p}-${Date.now()}-${seq++}`;

/** Append a user message + open a fresh assistant segment for the reply. */
export function beginTurn(state: TimelineState, text: string): TimelineState {
  const userMsg: UserMessageEvent = {
    type: 'user_message',
    content: text,
    id: genId('u'),
  };
  const assistantId = genId('a');
  const assistantMsg: AssistantMessageEvent = {
    type: 'assistant_message',
    content: '',
    id: assistantId,
  };
  return {
    items: [...state.items, userMsg, assistantMsg],
    activeAssistantId: assistantId,
  };
}

function appendDelta(state: TimelineState, text: string): TimelineState {
  // If no assistant segment is open (e.g. text after a tool call), open one.
  if (!state.activeAssistantId) {
    const assistantId = genId('a');
    const seg: AssistantMessageEvent = {
      type: 'assistant_message',
      content: text,
      id: assistantId,
    };
    return { items: [...state.items, seg], activeAssistantId: assistantId };
  }
  return {
    ...state,
    items: state.items.map((it) =>
      it.type === 'assistant_message' && it.id === state.activeAssistantId
        ? { ...it, content: it.content + text }
        : it,
    ),
  };
}

/** Locate a tool-call item by id and apply a patch, preserving order. */
function patchToolCall(
  state: TimelineState,
  toolCallId: string,
  patch: Partial<ToolCallItem>,
): TimelineState {
  return {
    ...state,
    items: state.items.map((it) =>
      it.type === 'tool_call' && it.toolCallId === toolCallId
        ? { ...it, ...patch }
        : it,
    ),
  };
}

/**
 * Fold one stream event into the timeline. Pure — returns a new state.
 * A tool_call_proposed closes the active assistant segment so any text that
 * arrives after the tool starts a NEW segment (honest interleaving).
 */
export function applyStreamEvent(
  state: TimelineState,
  evt: ConverseStreamEvent,
): TimelineState {
  switch (evt.type) {
    case 'start':
      // Turn already opened by beginTurn(); nothing to add.
      return state;

    case 'delta':
      return appendDelta(state, evt.text);

    case 'tool_call_proposed': {
      const item: ToolCallItem = {
        type: 'tool_call',
        id: `tc-${evt.toolCallId}`,
        toolCallId: evt.toolCallId,
        tool: evt.tool,
        args: evt.args,
        taint: evt.taint,
        quadrant: evt.quadrant,
        rationale: evt.rationale,
        phase: 'proposed',
      };
      // Close the active assistant segment; text after the tool opens a new one.
      return {
        items: [...state.items, item],
        activeAssistantId: null,
      };
    }

    case 'tool_call_decision':
      return patchToolCall(state, evt.toolCallId, {
        phase: 'decided',
        decision: evt.decision,
        mode: evt.mode,
        reason: evt.reason,
        overridable: evt.overridable,
      });

    case 'tool_call_result':
      return patchToolCall(state, evt.toolCallId, {
        phase: 'done',
        ok: evt.ok,
        results: evt.results,
        expandable: evt.expandable,
      });

    case 'tool_call_skipped':
      return patchToolCall(state, evt.toolCallId, {
        phase: 'skipped',
        skippedWhy: evt.why,
      });

    case 'done':
    case 'error':
      // Terminal converse events — handled by the hook (status/error), not
      // the timeline. Leave items untouched.
      return state;

    default:
      return state;
  }
}
