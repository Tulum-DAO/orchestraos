// Recent-4 agent chips for the dev-mode topbar (desktop).
// Spec: docs/superpowers/specs/2026-07-17-devmode-multi-agent-ux.md
// Click = switch to that agent's dev view. Dot shows live activity:
// green=ready(idle), amber pulse=working, blue=DECISION WAITING (a real menu
// on screen).
//
// ONE TRUTH SOURCE (the operator field-catch 2026-08-18, status-mismatch-investigator
// msg_dfd83a9e, gm-ratified): the dot binds to the v2 detector status from
// useAgents() — the SAME feed the detail modal reads — via chipStateFor().
// The old pane-scrape classifier (useAgentActivity -> classifyActivity) is
// RETIRED: its busy-marker vocabulary predated fable-5 chrome ('esc to
// interrupt' gone, gerund-bound spinner/timer regexes miss task-name
// spinners), so working agents read GREEN whenever no ◼-todo was visible,
// and hysteresis held the wrong green. Killing it also removes the 7s
// per-chip output-poll fan-out (4-20 fetches/7s).

import clsx from 'clsx';
import { useRecentAgents } from '../stores/recentAgents';
import { normalizeAgentState, STATE_STYLE } from '../lib/agentStatus';
import { useAgents } from '../hooks/useAgents';

// Round-2 fix (the operator field catch, investigator msg_918c2f95): the first fix
// unified the DATA source but kept a second COLOR vocabulary — stranded/
// waiting collapsed into blue (reserved for pending-menu) and working was
// amber vs the detail's orange. Now the dot CLASS comes from STATE_STYLE
// itself (the one palette every surface shares); blue is ONLY pending-menu.
export type ChipState = { dot: string; label: string };

const MENU_BLUE: ChipState = { dot: 'bg-blue-400', label: 'decision waiting' };

export function ActivityDot({ state }: { state: ChipState }) {
  return (
    <span
      className={clsx('inline-block w-2 h-2 rounded-full shrink-0', state.dot)}
      title={state.label}
    />
  );
}

/// Detector status -> chip dot. Exported for parity checks: the ONLY mapping
/// between /api/agents and the chip — and it delegates color to STATE_STYLE,
/// so chip and detail CANNOT diverge per-state.
export function chipStateFor(status: string | undefined, hasPendingMenu: boolean): ChipState {
  if (hasPendingMenu) return MENU_BLUE;        // blue = pending menu ONLY (msg_0c2bd052)
  const s = STATE_STYLE[normalizeAgentState(status)];
  return { dot: s.dot, label: s.label };
}

export function RecentAgentChips({ currentId }: { currentId: string }) {
  const recency = useRecentAgents((s) => s.recency);
  const requestFocus = useRecentAgents((s) => s.requestFocus);
  // Filter dead agents out of the chips: recency (localStorage) has no
  // liveness awareness, so killed agents lingered as chips. Recency ids are a
  // MIX of agent.id (AgentCard bumps) and tmux session name (WebTerminal
  // bumps), and 7 agents have id != tmux_session (+ an "unregistered:" prefix
  // form), so the alive-set must cover all three keys or terminal-driven live
  // agents get wrongly hidden.
  const { data: agentsData } = useAgents();
  const aliveIds = new Set<string>();
  // ONE truth: detector status + has_pending_menu per chip, keyed by every id
  // form a chip might use (agent.id + tmux_session + unregistered: form) so
  // the state lands regardless of which key recency stored.
  const chipStates = new Map<string, ChipState>();
  // Retired-generation rows can share the canonical `id` (e.g. the retired
  // gen-2 row keeps id "rotation-autonomy-builder" next to the live gen-3
  // canonical row). Last-writer-wins here let a DEAD row's offline-grey
  // clobber the live row's real state (the operator field catch 2026-09-02: working
  // chip rendered grey). Rule: a non-alive row may only seed a key no alive
  // row has claimed; an alive row always wins.
  const aliveKeyed = new Set<string>();
  for (const a of agentsData?.agents || []) {
    const st = chipStateFor(a.status, !!a.has_pending_menu);
    const keys: string[] = [];
    if (a.id) keys.push(a.id);
    if (a.tmux_session) keys.push(a.tmux_session);
    if (typeof a.id === 'string' && a.id.startsWith('unregistered:')) {
      keys.push(a.id.slice('unregistered:'.length));
    }
    for (const k of keys) {
      if (a.alive || !aliveKeyed.has(k)) chipStates.set(k, st);
      if (a.alive) aliveKeyed.add(k);
    }
    if (!a.alive) continue;
    for (const k of keys) aliveIds.add(k);
  }
  const chips = recency
    .filter((id) => id !== currentId && aliveIds.has(id))
    .slice(0, 20);
  const stateFor = (id: string): ChipState =>
    chipStates.get(id) || { dot: STATE_STYLE.unknown.dot, label: STATE_STYLE.unknown.label };

  if (chips.length === 0) return null;

  return (
    // Wraps up to 20 chips: 2 rows on wide screens, 3 on ~1440px. max-h caps
    // at three rows so a pathological localStorage state can't grow further.
    <div className="hidden md:flex flex-wrap items-center content-center gap-1.5 min-w-0 flex-1 justify-center px-3 max-h-[104px] overflow-hidden">
      {chips.map((id) => (
        <button
          key={id}
          onClick={(e) => {
            e.stopPropagation();
            requestFocus(id);
          }}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-neutral-700 bg-neutral-800/60 hover:bg-neutral-700 hover:border-neutral-500 transition-colors max-w-[160px]"
          title={`Switch to ${id}`}
        >
          <ActivityDot state={stateFor(id)} />
          <span className="text-[11px] text-neutral-300 truncate">{id}</span>
        </button>
      ))}
    </div>
  );
}
