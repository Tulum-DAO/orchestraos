/**
 * AgentRail — the Workbench left rail: Needs you / Working / Idle (PLAN v3.2, C7).
 *
 * Grouping lives in lib/agentRail.ts (pure, unit-tested); this file is render only. The feed is
 * /api/agents via useAgents() — the one-truth detector source guarded by
 * dashboard/scripts/guard-one-truth.sh — and the dot colours come from STATE_STYLE via
 * chipStateFor(), so the rail CANNOT drift from the chips or the agent detail.
 *
 * MEASURED against the live fleet (2026-10-06): 320 agents, of which 18 idle + 3 working are in
 * the three sections and 299 are offline/parked/retired. So the three sections are the default
 * view and everything else sits behind one collapsed row — a 320-row rail is not navigation.
 */
import { useMemo, useState } from 'react';
import clsx from 'clsx';
import { useAgents } from '../../hooks/useAgents';
import { groupForRail, providersVary, PROVIDER_ABBR, PROVIDER_LABEL, type RailAgent, type RailGroups } from '../../lib/agentRail';
import { agentRowsFrom } from '../../lib/api';
import { chipStateFor, ActivityDot } from '../RecentAgentChips';
import { useFeedHealth } from '../../hooks/useFeedHealth';
import { lastSeenLabel, type FeedVerdict } from '../../lib/feedLiveness';

const SECTIONS: { key: keyof Omit<RailGroups, 'other'>; label: string; badge?: boolean }[] = [
  { key: 'needsYou', label: 'Needs you', badge: true },
  { key: 'working', label: 'Working' },
  { key: 'idle', label: 'Idle' },
];


function RailRow({ agent, active, onPick, feed, showProvider }: { agent: RailAgent; active: boolean; onPick: (id: string) => void; feed: FeedVerdict; showProvider: boolean }) {
  const state = chipStateFor(agent.status, !!agent.has_pending_menu, feed);
  const provKey = agent.provider?.toLowerCase();
  const prov = showProvider && provKey ? PROVIDER_ABBR[provKey] : undefined;
  const unread = agent.unread ?? 0;
  return (
    <button
      type="button"
      onClick={() => onPick(agent.id)}
      aria-current={active ? 'true' : undefined}
      className={clsx(
        'w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-sm min-w-0',
        'hover:bg-neutral-800/70 focus:outline-none focus-visible:ring-1 focus-visible:ring-neutral-500',
        active && 'bg-neutral-800',
      )}
      title={`${agent.name || agent.id} — ${state.label}`}
    >
      <ActivityDot state={state} />
      <span className="truncate flex-1 min-w-0">{agent.name || agent.id}</span>
      {prov && (
        <span
          className="shrink-0 text-[10px] font-medium text-neutral-500 tabular-nums"
          title={`Runs on ${(provKey && PROVIDER_LABEL[provKey]) || agent.provider}`}
        >
          {prov}
        </span>
      )}
      {unread > 0 && (
        <span className="shrink-0 min-w-4 px-1 rounded-full bg-neutral-700 text-[10px] text-neutral-200 text-center tabular-nums">
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </button>
  );
}

export function AgentRail({ currentId, onPick }: { currentId?: string; onPick: (id: string) => void }) {
  const { data, isLoading, isError } = useAgents();
  const feed = useFeedHealth();
  const [showOther, setShowOther] = useState(false);

  const groups = useMemo(() => {
    // Tolerates BOTH response shapes via the one shared helper — this file used to accept
    // only `{agents:[...]}` and rendered three empty sections on a bare array.
    const rows: RailAgent[] = (agentRowsFrom(data) ?? []).map((a: Record<string, unknown>) => ({
      id: String(a.id),
      name: a.name as string | undefined,
      status: a.status as string | undefined,
      has_pending_menu: !!a.has_pending_menu,
      unread: (a.inbox_count as number | undefined) ?? 0,
      provider: a.runtime as string | undefined,
    }));
    // Name order inside a section; the section itself is the priority signal.
    rows.sort((x, y) => (x.name || x.id).localeCompare(y.name || y.id));
    return groupForRail(rows);
  }, [data]);

  // A column that prints the SAME two letters on every row tells the reader nothing and
  // costs a column of width to do it. The badge earns its place only when it distinguishes
  // one row from another, so it appears when at least two distinct runtimes are on screen.
  // A row with no runtime is not a second value: 138 of 320 live rows carry none, and
  // counting "unknown" as variety would make the badge permanent by accident.
  const badgesVary = useMemo(
    () => providersVary([...groups.needsYou, ...groups.working, ...groups.idle, ...groups.other]),
    [groups],
  );

  // An empty rail and a BROKEN rail must not look the same: a failed feed says so, because
  // "no agents need you" and "we could not ask" are different facts.
  if (isError) {
    return (
      <nav className="w-full p-3 text-sm text-red-400" aria-label="Agents">
        Could not load agents. The rail is not empty — it is unknown.
      </nav>
    );
  }
  // "Loading" is only honest while a first answer is still PLAUSIBLY coming. With the API down
  // at page load there is no error and no data, so this branch sat on "Loading agents…"
  // INDEFINITELY — measured against a stopped API. An eternal spinner is the same lie as a stale
  // colour: it says "wait" when the truth is "we cannot ask". After the threshold the verdict
  // below takes over and says so.
  if (isLoading && !data && feed.health !== 'disconnected') {
    return <nav className="w-full p-3 text-sm text-neutral-500" aria-label="Agents">Loading agents…</nav>;
  }

  return (
    <nav className="w-full flex flex-col gap-3 p-2 overflow-y-auto" aria-label="Agents">
      {/* The whole column says it ONCE, so a reader does not have to infer an outage from every
          row having gone grey. The sections below are the LAST thing we were told, not now. */}
      {feed.health !== 'live' && (
        <p className="mx-2 px-2 py-1.5 rounded border border-neutral-700 bg-neutral-900 text-[11px] text-neutral-400" role="status">
          {feed.health === 'disconnected' ? 'Not connected to the fleet.' : 'Connection lost.'}{' '}
          {feed.lastSeenAt ? `Showing what we last saw — ${lastSeenLabel(feed.lastSeenAt)}.` : 'No data has arrived yet.'}
        </p>
      )}
      {SECTIONS.map(({ key, label, badge }) => {
        const rows = groups[key];
        return (
          <section key={key} aria-labelledby={`rail-${key}`}>
            <h2
              id={`rail-${key}`}
              className="flex items-center gap-2 px-2 pb-1 text-[11px] uppercase tracking-wide text-neutral-500"
            >
              <span>{label}</span>
              {badge && rows.length > 0 && (
                <span className="px-1.5 rounded-full bg-amber-400/20 text-amber-300 text-[10px] tabular-nums">
                  {rows.length}
                </span>
              )}
              {!badge && <span className="text-neutral-600 tabular-nums">{rows.length}</span>}
            </h2>
            {rows.length === 0 ? (
              <p className="px-2 py-1 text-xs text-neutral-600">
                {key === 'needsYou' ? 'Nothing waiting on you.' : `No ${label.toLowerCase()} agents.`}
              </p>
            ) : (
              <div className="flex flex-col">
                {rows.map((a) => (
                  <RailRow key={a.id} agent={a} active={a.id === currentId} onPick={onPick} feed={feed} showProvider={badgesVary} />
                ))}
              </div>
            )}
          </section>
        );
      })}

      {groups.other.length > 0 && (
        <section aria-labelledby="rail-other">
          <button
            type="button"
            id="rail-other"
            onClick={() => setShowOther((v) => !v)}
            className="w-full flex items-center gap-2 px-2 py-1 text-[11px] uppercase tracking-wide text-neutral-500 hover:text-neutral-300"
            aria-expanded={showOther}
          >
            <span>{showOther ? '▾' : '▸'}</span>
            <span>Not running</span>
            <span className="text-neutral-600 tabular-nums">{groups.other.length}</span>
          </button>
          {showOther && (
            <div className="flex flex-col">
              {groups.other.map((a) => (
                <RailRow key={a.id} agent={a} active={a.id === currentId} onPick={onPick} feed={feed} showProvider={badgesVary} />
              ))}
            </div>
          )}
        </section>
      )}
    </nav>
  );
}
