/**
 * OrchestraOS V2 — Jarvis /v2/converse contract (FROZEN).
 *
 * Source of truth: docs/HANDOFF_assistant-ui-dev.md "THE CONTRACT".
 *
 *   POST /v2/converse {user, thread, channel, text}   — LOCKED
 *   Auth: X-Jarvis-Token header (server checks ~/.config/jarvis/token)
 *
 * Conversation state is keyed (user, thread). `channel` is a RENDERING
 * attribute ONLY — never scope state by channel. The dashboard bubble
 * uses channel:"bubble" and shares (user, thread) with Telegram for
 * continuity.
 *
 * B2 UPDATE: the tool-call event/stream schema is now DEFINED below
 * (congruence DEC-1785117307, orchestra-builder-v2 + agy-ops APPROVE,
 * 2026-07-26). Tool-call events are ADDITIONAL types multiplexed on the
 * SAME /v2/converse SSE stream, keyed to the same messageId. Spec:
 * .workspace/proposals/tool-call-event-schema-spec.md.
 */

/** The frozen request body. Do not add/rename fields. */
export interface ConverseRequest {
  /** Stable user identity (e.g. "operator"). Half of the state key. */
  user: string;
  /** Conversation thread id. Other half of the state key. */
  thread: string;
  /** Rendering surface only — "bubble" | "telegram" | ... Never scopes state. */
  channel: string;
  /** The user's message text. */
  text: string;
}

/** Header name carrying the Jarvis auth token. */
export const JARVIS_TOKEN_HEADER = 'X-Jarvis-Token';

/**
 * Converse SSE envelope (B1). Streamed as `event: <type>` + JSON `data:`.
 * Message-level only. This is the "pick one and spec it" transport choice
 * for converse: SSE.
 */
export type ConverseEvent =
  | ConverseStartEvent
  | ConverseDeltaEvent
  | ConverseDoneEvent
  | ConverseErrorEvent;

/** Emitted once when the assistant turn begins. */
export interface ConverseStartEvent {
  type: 'start';
  /** Server-assigned id for the assistant message being produced. */
  messageId: string;
  user: string;
  thread: string;
  channel: string;
}

/** A chunk of assistant text. Concatenate `text` in arrival order. */
export interface ConverseDeltaEvent {
  type: 'delta';
  messageId: string;
  text: string;
}

/** Terminal success event for the turn. */
export interface ConverseDoneEvent {
  type: 'done';
  messageId: string;
  /** Why the turn ended, e.g. "stop". */
  finishReason: string;
}

/** Terminal error event for the turn. */
export interface ConverseErrorEvent {
  type: 'error';
  message: string;
}

/* ===========================================================================
 * TOOL-CALL EVENTS (B2) — congruence DEC-1785117307.
 *
 * Multiplexed onto the SAME converse SSE stream, interleaved with `delta`s,
 * all under one `messageId`. Each tool call has a `toolCallId` correlating its
 * lifecycle: tool_call_proposed → tool_call_decision → (result | skipped).
 *
 * These render the SECURITY GATE's decision (D1–D8), not just a spinner: a
 * write under untrusted context is shown as PROPOSED then BLOCKED/SKIPPED with
 * NO approve path (D8 lethal quadrant is never overridable). In read-only V1
 * nothing mutates (shadow executes nothing), so writes surface as skipped.
 * ======================================================================== */

/** Context-trust and 2×2 quadrant at call time (mirrors the sidecar). */
export type ToolTaint = 'trusted' | 'untrusted';
export type ToolQuadrant =
  | 'read_trusted' | 'read_untrusted'
  | 'write_trusted' | 'write_untrusted'; // write_untrusted = lethal (D8)

/** The model proposes a tool call. Emitted for EVERY call (reads included). */
export interface ToolCallProposedEvent {
  type: 'tool_call_proposed';
  messageId: string;
  toolCallId: string;
  tool: string;
  /** PII-redacted at emit time (open decision A → redact). */
  args: Record<string, unknown>;
  taint: ToolTaint;
  quadrant: ToolQuadrant;
  rationale?: string;
}

/** The policy gate's ruling, emitted before any execution. */
export interface ToolCallDecisionEvent {
  type: 'tool_call_decision';
  messageId: string;
  toolCallId: string;
  decision: 'allow' | 'block' | 'require_human';
  mode: 'shadow' | 'live';
  /** e.g. "read" | "lethal_quadrant" | "shadow_mode_write" | "not_graduated" | "graduated" */
  reason: string;
  /** ALWAYS false for the lethal quadrant (D8). */
  overridable: boolean;
}

/** Terminal event for a call that actually EXECUTED. Maps to ToolCallCard. */
export interface ToolCallResultEvent {
  type: 'tool_call_result';
  messageId: string;
  toolCallId: string;
  ok: boolean;
  /** Maps directly to ToolCallCard.results. */
  results: string[];
  expandable?: string;
}

/** Terminal event for a call that did NOT execute (shadow/blocked/lethal/human). */
export interface ToolCallSkippedEvent {
  type: 'tool_call_skipped';
  messageId: string;
  toolCallId: string;
  why:
    | 'shadow'
    | 'blocked_lethal'
    | 'not_graduated'
    | 'human_rejected'
    | 'human_timeout';
}

/** Any tool-call lifecycle event on the converse stream. */
export type ToolCallEvent =
  | ToolCallProposedEvent
  | ToolCallDecisionEvent
  | ToolCallResultEvent
  | ToolCallSkippedEvent;

/* ===========================================================================
 * ACTIVITY EVENT (B3) — the honest activity pill's server signal.
 *
 * Jarvis on subscription auth emits NO native reasoning tokens, so this is an
 * ACTIVITY/STATUS signal (retrieving → thinking → answering), NOT synthesized
 * <thinking> theater. This shape is BLESSED as the Track-A target (B3 congruence
 * DEC-1785531254). The CLIENT consumes it forward-compatibly IF present; when
 * absent, the pill derives phase from the stream lifecycle. This UI does NOT
 * implement the server emitter — Track A (services/jarvis :5060) owns that.
 * ======================================================================== */
export type ActivityPhase = 'retrieving' | 'thinking' | 'answering';

/** Optional per-turn activity/status signal. Consumer-only in this client. */
export interface ActivityEvent {
  type: 'activity';
  messageId: string;
  phase: ActivityPhase;
  /** Optional human label, e.g. "reading tmux + state". */
  label?: string;
}

/** The full set of events that can arrive on the /v2/converse SSE stream. */
export type ConverseStreamEvent = ConverseEvent | ToolCallEvent | ActivityEvent;

/* ===========================================================================
 * SAVED CONVERSATIONS (Phase 1) — thread list + replay.
 *
 * The real /v2 already persists turns keyed (user, thread) in jarvis-ledger.db.
 * These add the DISCOVERY + REPLAY surface. Server gaps (G1–G4) are Track A;
 * the client builds against this contract in mock mode. the operator rulings honored:
 *   - per-session threads with easy resume
 *   - tool-call/gate lifecycle is PERSISTED, so a resumed thread REPLAYS gate
 *     cards (ThreadReplayTurn.toolCalls), not text-only
 *   - source channels tracked (bubble/page/telegram/voice) for continuity
 *   - soft-archive (archived flag); hard-delete is gated later
 * ======================================================================== */
export type ConversationChannel = 'bubble' | 'page' | 'telegram' | 'voice';

/** A row in the thread rail (GET /v2/threads). */
export interface ThreadSummary {
  thread: string;
  /** Auto-generated (Phase 2) or first-line fallback (Phase 1). */
  title: string;
  /** Short preview of the latest turn. */
  preview: string;
  updatedAt: string;
  turnCount: number;
  /** Which surfaces this thread has been touched from. */
  sourceChannels: ConversationChannel[];
  archived: boolean;
  pinned?: boolean;
}

/** A persisted tool call replayed with a resumed thread (gate card, B2 shape). */
export interface ThreadToolCall {
  toolCallId: string;
  tool: string;
  args: Record<string, unknown>;
  taint: ToolTaint;
  quadrant: ToolQuadrant;
  rationale?: string;
  decision?: 'allow' | 'block' | 'require_human';
  mode?: 'shadow' | 'live';
  reason?: string;
  overridable?: boolean;
  ok?: boolean;
  results?: string[];
  skippedWhy?: ToolCallSkippedEvent['why'];
}

/** One replayed turn (GET /v2/threads/{thread}). */
export interface ThreadReplayTurn {
  role: 'user' | 'assistant';
  text: string;
  createdAt: string;
  messageId?: string;
  channel?: ConversationChannel;
  /** Persisted gate cards for this turn (assistant turns only). */
  toolCalls?: ThreadToolCall[];
  /** For a voice-origin turn: playable audio (the operator ruling — retain audio). */
  audioUrl?: string;
}

/** Full thread replay payload. */
export interface ThreadDetail {
  thread: string;
  title: string;
  turns: ThreadReplayTurn[];
}
