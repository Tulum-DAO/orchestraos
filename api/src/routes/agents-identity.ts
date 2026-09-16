/**
 * R3 precedence law (visibility audit D1, DEC-pending gm gate):
 * identity fields flow DOWN from DB/registry (`def`) ONLY; a per-agent
 * state/<id>.json contributes runtime fields only. Applied AFTER the route's
 * `...def, ...state` spreads so a spawn-time stub ({name:'', tier:'T2',
 * status:'spawning'} — spawn-agent.sh, never updated) can never clobber
 * identity again. `status: 'spawning'` is freshness-gated: past the grace
 * window it derives from liveness instead of lying until the file is deleted.
 */

// A real spawn is interactive within a couple of minutes; 10 min is generous
// for slow boots without letting a dead stub claim 'spawning' for days.
export const SPAWNING_GRACE_MS = 10 * 60 * 1000;

/**
 * Liveness-pre-union fix (gm msg_7d936165, R3 class): observed local liveness
 * is EVIDENCE and beats the machine label. A DB-union seat with a NULL/missing
 * machine must not read alive:false while its tmux session is live on this box.
 * A live local session also settles the machine label to 'vps'; a dead seat
 * keeps its label (or 'unknown' when there is none) — never fabricated.
 */
export function resolveMachineAndLiveness(
  machine: string | undefined,
  tmuxSession: string,
  localSessions: Set<string>,
): { machine: string; alive: boolean } {
  if (localSessions.has(tmuxSession)) {
    return { machine: 'vps', alive: true };
  }
  return { machine: machine || 'unknown', alive: false };
}

export function applyIdentityPrecedence(
  agent: Record<string, unknown>,
  def: Record<string, unknown>,
  id: string,
  tmuxSession: string,
  machine: string,
  alive: boolean,
): void {
  agent.id = id;
  agent.name = (def.name as string) || id;
  agent.tier = (def.tier as string) || 'T2';
  agent.machine = machine;
  agent.tmux_session = tmuxSession;
  agent.always_on = (def.always_on as boolean) || false;

  if (agent.status === 'spawning') {
    const t = Date.parse((agent.spawned_at as string) || '');
    if (Number.isNaN(t) || Date.now() - t > SPAWNING_GRACE_MS) {
      agent.status = alive ? 'idle' : 'offline';
    }
  }
}
