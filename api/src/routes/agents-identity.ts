/**
 * R3 precedence law (visibility audit D1, DEC-pending gm gate):
 * identity fields flow DOWN from DB/registry (`def`) ONLY; a per-agent
 * state/<id>.json contributes runtime fields only. Applied AFTER the route's
 * `...def, ...state` spreads so a spawn-time stub ({name:'', tier:'T2',
 * status:'spawning'} — spawn-agent.sh, never updated) can never clobber
 * identity again. `status: 'spawning'` is freshness-gated: past the grace
 * window it derives from liveness instead of lying until the file is deleted.
 */
import { hierarchyFieldsFor } from './agentHierarchy.js';

// A real spawn is interactive within a couple of minutes; 10 min is generous
// for slow boots without letting a dead stub claim 'spawning' for days.
export const SPAWNING_GRACE_MS = 10 * 60 * 1000;

/**
 * Foreign tmux sessions as `unregistered:<name>` chips — ONLY when the operator opted in
 * ([dashboard] show_unregistered_sessions = true). tmux is host-global (gm ruling
 * msg_9f04c5f0): a second instance beside a live fleet listed, and could message, the
 * other instance's seats. Pure; the route passes the live session set.
 */
export function discoverUnregistered(localSessions: Set<string>, registered: Set<string>, enabled: boolean): any[] {
  if (!enabled) return [];
  const out: any[] = [];
  for (const session of localSessions) {
    if (registered.has(session) || session.startsWith('session-')) continue;
    out.push({
      id: `unregistered:${session}`,
      tier: 'T2',
      name: session,
      machine: 'vps',
      tmux_session: session,
      always_on: false,
      status: 'running',
      current_task: null,
      last_updated: null,
      tmux_alive: true,
      alive: true,
      inbox_count: 0,
      machine_status: 'online',
      unregistered: true,
    });
  }
  return out;
}

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
  // tier AND reports_to, from the SAME helper the row builder used — re-asserted here for the
  // very reason this function exists: the `...def, ...state` spreads run AFTER the row literal,
  // so a raw registry value lands back on top of the normalised one. Without this, a def
  // carrying `reports_to: ''` was normalised to undefined by the row builder and then had the
  // empty string put straight back, so the ROUTE emitted the exact value the normalisation
  // exists to prevent while the unit test on the helper stayed green. reports_to is an identity
  // field and was simply missing from this law.
  Object.assign(agent, hierarchyFieldsFor(def));
  // An absent parent must not serialise at all; Object.assign would leave the key present
  // with value undefined, which JSON.stringify drops but Object.keys and `in` do not.
  if (agent.reports_to === undefined) delete agent.reports_to;
  agent.machine = machine;
  agent.tmux_session = tmuxSession;
  agent.always_on = (def.always_on as boolean) || false;

  // Retirement is a REGISTRY fact, so the same precedence law applies to it.
  //
  // Every lineage rotation leaves a `seat-gN` row behind: the registry marks it
  // retired, but its state/<id>.json is the frozen spawn stub and still says
  // 'spawning'. The route's `...def, ...state` spread lets that stub win, and
  // the grace branch below then ages 'spawning' into 'offline' — so a seat that
  // was deliberately decommissioned surfaced to every client as a down agent.
  // That is the whole mechanism behind the org chart rooting at the retired
  // gm-g2 and the header counting two phantom down agents; `build-g2` joined
  // them the moment this seat rotated. Keyed off registry status, never off the
  // `-gN` shape of an id, because the next ghost will not be a `-gN`.
  const registryStatus = String(def.status || '').toLowerCase();
  if (registryStatus === 'retired' || registryStatus === 'archived') {
    agent.status = 'retired';
    agent.retired = true;
    agent.activity = `Decommissioned (registry: ${registryStatus})`;
    return;
  }
  agent.retired = false;

  if (agent.status === 'spawning') {
    const t = Date.parse((agent.spawned_at as string) || '');
    if (Number.isNaN(t) || Date.now() - t > SPAWNING_GRACE_MS) {
      agent.status = alive ? 'idle' : 'offline';
    }
  }
}

/**
 * An agent id reduced to the canonical root it refers to, by stripping a leading discovery
 * prefix: `unregistered:<session>` (foreign tmux sessions, above) or `mac:` (the mac gateway).
 *
 * Any lookup keyed on a ROOT — the client_description map, the generations/lineage tables —
 * must go through this, or a prefixed row misses a hit the row beside it gets. Anchored and
 * single-shot: an id is a lookup key, so `mac:` in the MIDDLE of a session name is part of the
 * name, not a prefix to be removed.
 */
export function baseAgentId(id: string): string {
  return id.replace(/^(?:unregistered|mac):/, '');
}
