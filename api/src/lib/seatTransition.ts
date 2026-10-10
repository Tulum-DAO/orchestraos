/**
 * A seat's reincarnation (POST /api/agent-state/:id/transition): the live tmux session is renamed
 * <id> -> <id>-transition so the successor can take the name, then spawn-agent.sh starts it.
 *
 * gm msg_75776be5: if the spawn FAILS, the seat must not be lost. Losing a seat is worse than a failed
 * spawn, so on failure the old session gets its name back (unless something now holds that name) and
 * the caller undoes its other steps. Deps are injected so this is testable without tmux.
 */
export interface TransitionDeps {
  /** Run `tmux <args>`; throws on failure. */
  tmux(args: string[]): void;
  /** Whether a tmux session with exactly this name exists now. */
  hasSession(name: string): boolean;
  /** Start the successor; calls back with an error if the spawn failed. */
  spawn(agentId: string, cb: (err: Error | null) => void): void;
}

export interface TransitionResult {
  ok: boolean;
  /** On failure: the old session has its own name back. */
  restored?: boolean;
  error?: string;
}

export function transitionName(agentId: string): string {
  return `${agentId}-transition`;
}

export function transitionSeat(agentId: string, deps: TransitionDeps, done: (r: TransitionResult) => void): void {
  let renamed = false;
  try {
    deps.tmux(['rename-session', '-t', agentId, transitionName(agentId)]);
    renamed = true;
  } catch { /* the session name may differ from the agent id: nothing to move aside */ }
  deps.spawn(agentId, (err) => {
    if (!err) { done({ ok: true }); return; }
    let restored = false;
    if (renamed && !deps.hasSession(agentId)) {
      try {
        deps.tmux(['rename-session', '-t', transitionName(agentId), agentId]);
        restored = true;
      } catch { /* the old session is gone too: nothing left to restore */ }
    }
    done({ ok: false, restored, error: err.message });
  });
}
