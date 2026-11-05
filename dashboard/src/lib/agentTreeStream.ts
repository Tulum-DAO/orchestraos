/**
 * State machine behind hooks/useAgentTree: GET /api/agents/:id/tree/stream (SSE `snapshot` frames
 * with the whole tree) with GET /tree polling as the fallback lane, like lib/transcriptStream.
 *   snapshot -> replaces the tree        end / 3 errors in a row -> poll every POLL_MS
 *   poll 404 -> gone (no Claude session on this box: render nothing, stop asking)
 *   poll failure -> interval doubles up to POLL_MAX_MS; a good poll resets it
 *   reset (new seat, ?all toggled) -> back to connecting
 */
import type { AgentTreeNode } from './agentTree';

export const POLL_MS = 5000;
export const POLL_MAX_MS = 60000;
const FALLBACK_AFTER_ERRORS = 3;

export type TreeStreamMode = 'connecting' | 'stream' | 'polling' | 'gone';

export interface TreeStreamState {
  mode: TreeStreamMode;
  tree: AgentTreeNode | null;
  errors: number;
  pollMs: number;
  /** The API echoed `all_finished` on a root, so it honours ?all=1 and the toggle may show. */
  supportsAll: boolean;
  /** Bumped on every poll result; the hook schedules the next poll when it changes. */
  seq: number;
  /** The request this state answers (seat + ?all), set by reset. The hook's poll lane acts only on its own. */
  key: string | null;
}

export type TreeStreamEvent =
  | { type: 'reset'; key?: string | null }
  | { type: 'open' }
  | { type: 'snapshot'; tree: AgentTreeNode | null }
  | { type: 'end' }
  | { type: 'error' }
  | { type: 'poll'; status: number; tree?: AgentTreeNode | null };

export function initialTreeStream(): TreeStreamState {
  return { mode: 'connecting', tree: null, errors: 0, pollMs: POLL_MS, supportsAll: false, seq: 0, key: null };
}

const withTree = (s: TreeStreamState, tree: AgentTreeNode | null): TreeStreamState => ({
  ...s, tree, supportsAll: s.supportsAll || (!!tree && typeof tree.all_finished === 'boolean'),
});

export function reduceTreeStream(s: TreeStreamState, ev: TreeStreamEvent): TreeStreamState {
  if (ev.type === 'reset') return { ...initialTreeStream(), key: ev.key ?? null };
  if (s.mode === 'gone') return s;
  switch (ev.type) {
    case 'open':
      return { ...s, errors: 0 };
    case 'snapshot':
      return { ...withTree(s, ev.tree), mode: s.mode === 'polling' ? 'polling' : 'stream', errors: 0 };
    case 'end':
      return { ...s, mode: 'polling', pollMs: POLL_MS };
    case 'error': {
      const errors = s.errors + 1;
      return errors >= FALLBACK_AFTER_ERRORS ? { ...s, errors, mode: 'polling', pollMs: POLL_MS } : { ...s, errors };
    }
    case 'poll': {
      const seq = s.seq + 1;
      if (ev.status === 404) return { ...s, seq, mode: 'gone', tree: null };
      if (ev.status >= 200 && ev.status < 300) return { ...withTree(s, ev.tree ?? null), seq, mode: 'polling', pollMs: POLL_MS };
      return { ...s, seq, mode: 'polling', pollMs: Math.min(s.pollMs * 2, POLL_MAX_MS) };
    }
  }
}

/** What the hook's poll lane does for `state` under the current request `key`: nothing ('off': streaming, disabled,
 *  or state left over from another seat or ?all), close the stream and poll once per seq ('poll'), or close it and
 *  stop ('stop': no Claude session). A stale state never touches the stream just opened for a new request. */
export function pollLane(state: TreeStreamState, key: string, enabled: boolean): 'off' | 'poll' | 'stop' {
  if (!enabled || state.key !== key) return 'off';
  if (state.mode === 'polling') return 'poll';
  if (state.mode === 'gone') return 'stop';
  return 'off';
}
