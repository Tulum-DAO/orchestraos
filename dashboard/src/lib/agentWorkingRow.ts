/**
 * agentWorkingRow.ts — what the agent page's working row says (components/agent/AgentWorkingRow.tsx draws it: the
 * agent's animated avatar, its name, and this line).
 *
 * It is the old status line's whole truth, in the shape of Arturo's working row: disconnected, updating, the live
 * step, working / stalled with how long, waiting / stranded, queued, stopped / crashed / offline / retired with
 * Resume where it helps (offersResume), idle, unknown. Pure, so every state is pinned by a test without a DOM.
 *
 * It reads the SAME state as the sidebar dot: the rail's status (railStatus over the gateway's chip and the
 * detector), the rail's pending-menu flag, and the same feed verdict, through normalizeAgentState.
 */
import { normalizeAgentState, offersResume, QUEUED_LINE, type LiveState } from './agentStatus';
import { railPendingMenu, railStatus, type AgentChip } from './agentChip';
import { isDegraded, lastSeenLabel, type FeedVerdict } from './feedLiveness';
import { stepElapsed, stepText, type AgentStep } from './liveStep';

/** The look of the line: its colour family, and whether its mark spins. */
export type RowTone = 'working' | 'needs' | 'queued' | 'down' | 'idle' | 'muted';
export type RowMark = 'spin' | 'hand' | 'dot' | 'off' | 'wifi';

export interface WorkingRow {
  /** Which truth this is (tests, and data-row-kind on the page). */
  kind: 'disconnected' | 'updating' | 'step' | 'waitingStep' | 'idleStep' | 'working' | 'waiting' | 'stranded'
    | 'queued' | 'down' | 'idle' | 'unknown';
  /** The state the dot shows, or null while the feed itself is the truth (disconnected, updating). */
  state: LiveState | null;
  text: string;
  /** How long, when known ("3m 4s"). */
  elapsed?: string;
  tone: RowTone;
  mark: RowMark;
  /** Offer Resume (stopped, crashed, offline; never retired). */
  resume: boolean;
}

export interface WorkingRowInput {
  /** The detector's status on the seat's /api/agents row. */
  status?: string;
  /** The gateway's chip on that row, when there is one: it decides the dot, so it decides this too. */
  chip?: AgentChip;
  /** The row's has_pending_menu (or pending_menu present). */
  pendingMenu?: boolean;
  feed?: FeedVerdict;
  stateAgeS?: number;
  tool?: string;
  step?: AgentStep | null;
  now?: number;
}

function elapsed(s?: number): string {
  if (!s || s < 0) return '';
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${Math.round(s % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

const DOWN: ReadonlySet<LiveState> = new Set<LiveState>(['stopped', 'crashed', 'offline', 'retired']);

/** The state the sidebar dot shows for this seat (lib/agentRail + components/workbench/AgentRail). */
export function dotState(i: Pick<WorkingRowInput, 'status' | 'chip' | 'pendingMenu'>): { state: LiveState; menu: boolean } {
  return { state: normalizeAgentState(railStatus(i.chip, i.status)), menu: railPendingMenu(i.chip, !!i.pendingMenu) };
}

export function workingRowFor(i: WorkingRowInput): WorkingRow {
  const feed = i.feed;
  // DISCONNECTED OUTRANKS EVERY AGENT STATE: with no live feed we cannot claim what it is doing.
  if (feed && isDegraded(feed)) {
    return { kind: 'disconnected', state: null, tone: 'muted', mark: 'wifi', resume: false,
      text: `${feed.health === 'disconnected' ? 'Not connected' : 'Connection lost'} · ${lastSeenLabel(feed.lastSeenAt)}` };
  }
  // UPDATING (back from a hidden tab): the state is minutes old until the refresh lands. Never a stale "Working · 3m".
  if (feed && feed.health === 'updating') {
    return { kind: 'updating', state: null, tone: 'muted', mark: 'spin', resume: false, text: 'Status updating…' };
  }

  const { state: st, menu } = dotState(i);
  const step = i.step;

  // A menu on its screen is a question already asked: the dot says needs you, so this does too.
  if (menu && !DOWN.has(st)) {
    return { kind: 'waiting', state: st, tone: 'needs', mark: 'hand', resume: false, text: 'Waiting for you to answer' };
  }

  // THE LIVE STEP, when the agent is up: what it is doing, in words. A down agent keeps its own line below.
  if (step && !DOWN.has(st)) {
    if (step.kind === 'tool' || step.kind === 'thinking') {
      const how = stepElapsed(step, i.now);
      return { kind: 'step', state: st, tone: 'working', mark: 'spin', resume: false, text: stepText(step), ...(how ? { elapsed: how } : {}) };
    }
    if (step.kind === 'waiting') {
      return { kind: 'waitingStep', state: st, tone: 'needs', mark: 'hand', resume: false, text: stepText(step) };
    }
    if (st !== 'queued' && st !== 'stranded') {
      return { kind: 'idleStep', state: st, tone: 'idle', mark: 'dot', resume: false, text: 'Idle · ready for a message' };
    }
  }

  if (st === 'working' || st === 'stalled') {
    const how = elapsed(i.stateAgeS);
    return { kind: 'working', state: st, tone: 'working', mark: 'spin', resume: false,
      text: `Working${i.tool ? ` · ${i.tool}` : ''}`, ...(how ? { elapsed: how } : {}) };
  }
  if (st === 'waiting') return { kind: 'waiting', state: st, tone: 'needs', mark: 'hand', resume: false, text: 'Waiting for you to answer' };
  if (st === 'stranded') return { kind: 'stranded', state: st, tone: 'needs', mark: 'hand', resume: false, text: 'An unsent draft is in its composer' };
  // queued: the CLI accepted the message while busy and ended the turn without running it. Not working, not idle.
  if (st === 'queued') return { kind: 'queued', state: st, tone: 'queued', mark: 'dot', resume: false, text: QUEUED_LINE };
  if (DOWN.has(st)) {
    const why = st === 'crashed' ? 'Crashed' : st === 'offline' ? 'Offline' : st === 'retired' ? 'Retired' : 'Stopped';
    return { kind: 'down', state: st, tone: 'down', mark: 'off', resume: offersResume(st),
      text: `${why} — it will not see a message until it is running.` };
  }
  if (st === 'idle') return { kind: 'idle', state: st, tone: 'idle', mark: 'dot', resume: false, text: 'Idle · at the prompt, ready for a message' };
  // unknown: claim NOTHING. An honest blank is better than a guess.
  return { kind: 'unknown', state: st, tone: 'muted', mark: 'dot', resume: false, text: 'State unknown' };
}
