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
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { MoreHorizontal, ChevronRight, ChevronDown } from 'lucide-react';
import clsx from 'clsx';
import { focusDecision } from '../../lib/decisions';
import { useAgents } from '../../hooks/useAgents';
import { groupForRail, providersVary, contextLabel, contextHigh, showsContext, sectionItems, rowLabel, PROVIDER_ABBR, PROVIDER_LABEL, type RailAgent, type RailGroups, type RailItem } from '../../lib/agentRail';
import { usePins, loadPinsOnce, togglePin, renameAgent, longPress } from '../../lib/railAgents';
import { agentRowsFrom } from '../../lib/api';
import { chipStateFor } from '../RecentAgentChips';
import { AgentAvatar } from '../avatar/AgentAvatar';
import AgentIdentityPill from '../avatar/AgentIdentityPill';
import { avatarHub, ensureAgent } from '../avatar/agentState';
import { providerMark } from '../avatar/brandMarks';
import { avatarStateFor } from '../../lib/avatarStatus';
import { useFeedHealth } from '../../hooks/useFeedHealth';
import { isDegraded, lastSeenLabel, type FeedVerdict } from '../../lib/feedLiveness';
import { asChip, contextLeftLabel, contextLeftLow, providerTitle, railStatus, railPendingMenu, kindLabel, type AgentChip } from '../../lib/agentChip';
import { useProviderMarks, type ProviderMarks } from '../../hooks/useProviderMarks';

const SECTIONS: { key: keyof Omit<RailGroups, 'other'>; label: string; badge?: boolean }[] = [
  { key: 'needsYou', label: 'Needs you', badge: true },
  { key: 'working', label: 'Working' },
  { key: 'idle', label: 'Idle' },
];


function RailRow({ agent, active, onPick, feed, showProvider, marks, label, pinnedNow, onRenamed }: { agent: RailAgent & { chip?: AgentChip }; active: boolean; onPick: (id: string) => void; feed: FeedVerdict; showProvider: boolean; marks: ProviderMarks; label: string; pinnedNow: boolean; onRenamed: () => void }) {
  const [menu, setMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const press = useMemo(() => longPress(() => setMenu(true)), []);   // one per row: it remembers a long-press across renders
  const state = chipStateFor(agent.status, !!agent.has_pending_menu, feed);
  const chip = agent.chip;
  const provKey = (chip?.provider || agent.provider)?.toLowerCase();
  // The gateway's chip names the provider by its logo on every row (names are ours, the mark says
  // who runs it). Without a chip, or before the marks load, the old two-letter badge stands in.
  // The avatar's own badge names the provider (its official mark) when it has one, so neither the small
  // logo nor the two-letter fallback is drawn beside it; its dot is the row's status dot.
  const badge = providerMark(provKey);
  const mark = !badge && chip && provKey ? marks[provKey] : undefined;
  const prov = !badge && !mark && (chip ? !!provKey : showProvider) && provKey ? PROVIDER_ABBR[provKey] : undefined;
  const provName = (provKey && (marks[provKey]?.label || PROVIDER_LABEL[provKey])) || agent.provider || '';
  const unread = agent.unread ?? 0;
  // Context used, for every runtime that reports one; a running seat with no reading shows a
  // dash (unknown), a seat that is not running shows nothing (lib/agentRail.ts showsContext).
  // With a chip: context LEFT of the window ("92% left"), amber when 20% or less is left.
  // A service or an external seat has no context to show; its kind says what it is instead.
  const kind = kindLabel(chip);
  const ctx = kind ? undefined : showsContext(agent)
    ? (chip ? contextLeftLabel(chip) : contextLabel(agent.context_pct)) : undefined;
  const ctxHigh = chip ? contextLeftLow(chip) : contextHigh(agent.context_pct);
  const shownName = agent.display_name || agent.name || agent.id;
  return (
    <div className="group relative" data-rail-row={agent.id} {...press}
         onContextMenu={(e) => { if (window.matchMedia('(hover: none)').matches) e.preventDefault(); }}>
    {renaming ? (
      <RenameField agent={agent} active={active} initial={shownName} onDone={(ok) => { setRenaming(false); if (ok) onRenamed(); }} onError={setError} />
    ) : (
    <button
      type="button"
      onClick={() => onPick(agent.id)}
      aria-current={active ? 'true' : undefined}
      className={clsx(
        'w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-sm min-w-0',
        'hover:bg-neutral-200/70 dark:hover:bg-neutral-800/70 focus:outline-none focus-visible:ring-1 focus-visible:ring-neutral-500',
        active && 'bg-neutral-200 dark:bg-neutral-800',
      )}
      title={`${agent.full_name || label} — ${state.label}`}
    >
      {/* 32px filling the row's padding (the negative margin), so the row is no taller than before. */}
      <span className={clsx('-my-1.5 shrink-0', active ? 'la-on-selected' : 'la-on-sidebar')}>
        <AgentAvatar agentId={agent.id} role="sidebar" size={32} selected={active} provider={provKey ?? null} label={agent.name || agent.id} />
      </span>
      {mark && (
        <span
          className="shrink-0 inline-flex w-3.5 h-3.5 [&>svg]:w-full [&>svg]:h-full text-neutral-700 dark:text-neutral-300"
          title={providerTitle(provName, chip)}
          aria-label={providerTitle(provName, chip)}
          role="img"
          data-testid="rail-provider-mark"
          dangerouslySetInnerHTML={{ __html: mark.svg }}
        />
      )}
      <span className="truncate flex-1 min-w-0">{label}</span>
      {kind && (
        <span className="shrink-0 text-[10px] text-neutral-600 dark:text-neutral-500" data-testid="rail-kind"
              title={kind === 'Service' ? 'A service: it takes no messages' : 'Run elsewhere: not checked from here'}>
          {kind}
        </span>
      )}
      {ctx !== undefined && (
        <span
          className={clsx('shrink-0 text-[10px] tabular-nums [@media(hover:hover)]:group-hover:invisible group-focus-within:invisible', ctxHigh ? 'text-amber-700 dark:text-amber-400' : 'text-neutral-600 dark:text-neutral-500')}
          title={ctx === null ? 'Context: no reading' : chip ? `Context: ${ctx}` : `Context used: ${ctx}`}
          data-testid="rail-context"
        >
          {ctx ?? '—'}
        </span>
      )}
      {prov && (
        <span
          className="shrink-0 text-[10px] font-medium text-neutral-600 dark:text-neutral-500 tabular-nums"
          title={providerTitle(provName, chip)}
        >
          {prov}
        </span>
      )}
      {unread > 0 && (
        <span className="shrink-0 min-w-4 px-1 rounded-full bg-neutral-300 dark:bg-neutral-700 text-[10px] text-neutral-800 dark:text-neutral-200 text-center tabular-nums">
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </button>
    )}
    {/* Hover (or keyboard focus) puts a ⋯ where the usage figure is; on a phone a long-press opens the same menu. */}
    {!renaming && (
      <button type="button" aria-label={`More for ${agent.full_name || label}`} aria-haspopup="menu" aria-expanded={menu}
              data-rail-more
              onClick={(e) => { e.stopPropagation(); setMenu((v) => !v); }}
              className={clsx('absolute right-1 top-1/2 -translate-y-1/2 w-6 h-6 grid place-items-center rounded text-neutral-600 dark:text-neutral-400',
                'hover:bg-neutral-300/70 dark:hover:bg-neutral-700/70',
                menu ? 'visible' : 'invisible [@media(hover:hover)]:group-hover:visible group-focus-within:visible')}>
        <MoreHorizontal size={14} />
      </button>
    )}
    {menu && (
      <RowMenu pinnedNow={pinnedNow} onClose={() => setMenu(false)}
               onPin={() => { setMenu(false); void togglePin(agent.id).then(setError); }}
               onRename={() => { setMenu(false); setError(null); setRenaming(true); }} />
    )}
    {error && <p className="px-2 pb-1 text-[11px] text-red-600 dark:text-red-400" role="alert">{error}</p>}
    </div>
  );
}

/** The row's menu: Pin/Unpin and Rename. Closes on a choice, Escape, or a click anywhere else. */
function RowMenu({ pinnedNow, onPin, onRename, onClose }: { pinnedNow: boolean; onPin: () => void; onRename: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: Event) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', esc);
    ref.current?.querySelector<HTMLButtonElement>('button')?.focus();
    return () => { document.removeEventListener('pointerdown', away, true); document.removeEventListener('keydown', esc); };
  }, [onClose]);
  const item = 'w-full text-left px-3 py-1.5 text-sm rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 focus:outline-none focus-visible:bg-neutral-100 dark:focus-visible:bg-neutral-800';
  return (
    <div ref={ref} role="menu" data-rail-menu onClick={(e) => e.stopPropagation()}
         className="absolute right-1 top-full z-30 mt-0.5 min-w-[9rem] p-1 rounded-md border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 shadow-lg">
      <button type="button" role="menuitem" className={item} onClick={onPin}>{pinnedNow ? 'Unpin' : 'Pin'}</button>
      <button type="button" role="menuitem" className={item} onClick={onRename}>Rename</button>
    </div>
  );
}

/** Rename in place: Enter saves, Escape (or leaving the field) cancels; a refusal shows its reason under the row. */
function RenameField({ agent, active, initial, onDone, onError }: { agent: RailAgent; active: boolean; initial: string; onDone: (ok: boolean) => void; onError: (e: string | null) => void }) {
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    const next = text.trim();
    if (!next || next === initial) { onDone(false); return; }
    setBusy(true);
    const r = await renameAgent(agent.id, next);
    setBusy(false);
    if (r.ok) { onError(null); onDone(true); } else onError(r.error);
  };
  return (
    <div className={clsx('w-full flex items-center gap-2 px-2 py-1 rounded', active && 'bg-neutral-200 dark:bg-neutral-800')} onClick={(e) => e.stopPropagation()}>
      <span className={clsx('-my-1 shrink-0', active ? 'la-on-selected' : 'la-on-sidebar')}>
        <AgentAvatar agentId={agent.id} role="sidebar" size={32} selected={active} label={initial} />
      </span>
      <input autoFocus value={text} disabled={busy} maxLength={40} aria-label={`New name for ${agent.full_name || initial}`} data-rail-rename
             onChange={(e) => { setText(e.target.value); onError(null); }}
             onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void save(); } else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onError(null); onDone(false); } }}
             onBlur={() => { if (!busy) { onError(null); onDone(false); } }}
             className="flex-1 min-w-0 text-sm px-1.5 py-0.5 rounded border border-neutral-300 dark:border-neutral-600 bg-white dark:bg-neutral-900 outline-none focus:border-neutral-500" />
    </div>
  );
}

/** A row of small live faces with their status dots; one click opens that agent. Used for pins and for a collapsed
 *  project group. More than `max` show as "+N". The faces carry no names, so hovering one (or focusing it from the
 *  keyboard) fades in a pill with its name, in the shape of the pill at the top of Arturo's page: it starts where
 *  the face is, with the same face in the same place, and runs to the right over the faces beside it. Clicking the
 *  pill opens that agent; leaving it lets it fade. */
/** Faces are the same size as the avatars on the agent rows. */
const FACE = 32;
/** The name pill's inset around its face (px), the agent identity pill's (.agent-pill in index.css): the pill is
 *  placed so its face lands exactly on the face under it, so the inset never moves the face. */
const PILL_INSET = 2;

function FaceRow({ agents, currentId, onPick, max = 8, label, indent = false }: { agents: RailAgent[]; currentId?: string; onPick: (id: string) => void; max?: number; label: string; indent?: boolean }) {
  const shown = agents.slice(0, max);
  const outer = useRef<HTMLDivElement>(null);
  const [pill, setPill] = useState<{ a: RailAgent; left: number; top: number; max: number; keyboard: boolean } | null>(null);
  const show = (a: RailAgent, el: HTMLElement, keyboard = false) => {
    if (!keyboard && !window.matchMedia('(hover: hover)').matches) return;   // a tap opens the agent; no pill
    // measured from the face itself (its canvas), not its button, so the pill's face covers it exactly
    const box = outer.current?.getBoundingClientRect(), r = (el.querySelector('canvas') ?? el).getBoundingClientRect();
    if (!box) return;
    const off = PILL_INSET;
    setPill({ a, left: r.left - box.left - off, top: r.top - box.top - off, max: box.right - r.left + off - 4, keyboard });
  };
  return (
    <div ref={outer} className="relative">
      {/* 8px side padding, the same as an agent row's, and faces with no padding of their own: the first face sits
          exactly where a row's avatar does. A collapsed group's faces row has no top/bottom padding either, so its
          first face is in the exact place the first row's avatar takes when the group is opened. */}
      <div className={clsx('flex items-center gap-1 px-2 overflow-x-auto', indent ? 'py-0' : 'py-1')} role="list" aria-label={label} onScroll={() => setPill(null)}>
        {shown.map((a) => (
          <button key={a.id} type="button" role="listitem" onClick={() => onPick(a.id)} data-rail-face={a.id}
                  aria-label={a.full_name || a.display_name || a.id}
                  onMouseEnter={(e) => show(a, e.currentTarget)}
                  onFocus={(e) => { if (e.currentTarget.matches(':focus-visible')) show(a, e.currentTarget, true); }}
                  onBlur={() => setPill((p) => (p?.keyboard ? null : p))}
                  // The selected (and hovered) face sits on a filled grey circle tight against it: the fill behind the
                  // face plus a 2px shadow of the same grey, so it marks the face without padding that would move it.
                  className={clsx('shrink-0 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-tone-400',
                    a.id === currentId
                      ? 'la-on-selected bg-neutral-200 dark:bg-neutral-800 shadow-[0_0_0_2px_var(--color-neutral-200)] dark:shadow-[0_0_0_2px_var(--color-neutral-800)]'
                      : 'la-on-sidebar hover:bg-neutral-100 dark:hover:bg-neutral-900 hover:shadow-[0_0_0_2px_var(--color-neutral-100)] dark:hover:shadow-[0_0_0_2px_var(--color-neutral-900)]')}>
            <AgentAvatar agentId={a.id} role="sidebar" size={FACE} selected={a.id === currentId} label={a.full_name || a.id} />
          </button>
        ))}
        {agents.length > max && <span className="shrink-0 text-[11px] text-neutral-600 dark:text-neutral-400 tabular-nums">+{agents.length - max}</span>}
      </div>
      {pill && (
        <AgentIdentityPill agentId={pill.a.id} name={pill.a.full_name || pill.a.display_name || pill.a.id} size={FACE}
          avatar={{ role: 'sidebar', selected: pill.a.id === currentId, label: pill.a.full_name || pill.a.id }}
          aria-hidden tabIndex={-1} data-rail-face-pill={pill.a.id}
          onClick={() => { setPill(null); onPick(pill.a.id); }}
          onMouseLeave={() => setPill(null)}
          style={{ left: pill.left, top: pill.top, maxWidth: pill.max }}
          className={clsx('rail-face-pill absolute z-20', pill.keyboard && 'pointer-events-none')} />
      )}
    </div>
  );
}

/** A project's group inside a section: its label and count; collapsed, its agents' faces (one click opens one);
 *  open, their rows by short name. Its open/closed state is remembered per section and project in this browser. */
function ProjectGroup({ section, project, agents, defaultOpen, currentId, onPick, children }: {
  section: string; project: { slug: string; label: string }; agents: RailAgent[]; defaultOpen: boolean; currentId?: string;
  onPick: (id: string) => void; children: ReactNode;
}) {
  const key = `orchestra.rail.group.${section}.${project.slug}`;
  const [open, setOpen] = useState<boolean>(() => {
    try { const v = localStorage.getItem(key); return v === null ? defaultOpen : v === '1'; } catch { return defaultOpen; }
  });
  const flip = (e: { stopPropagation: () => void }) => {
    e.stopPropagation();
    setOpen((v) => { try { localStorage.setItem(key, v ? '0' : '1'); } catch { /* this tab only */ } return !v; });
  };
  return (
    <div data-rail-group={project.slug}>
      <button type="button" onClick={flip} aria-expanded={open}
              className="w-full flex items-center gap-1.5 px-2 pt-1 pb-0.5 text-[11px] font-medium text-neutral-700 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-200">
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="truncate">{project.label}</span>
        <span className="text-neutral-500 dark:text-neutral-600 tabular-nums">{agents.length}</span>
      </button>
      {open ? <div className="flex flex-col">{children}</div>
        : <FaceRow agents={agents} currentId={currentId} onPick={onPick} indent label={`${project.label}: ${agents.length} agents`} />}
    </div>
  );
}

export function AgentRail({ currentId, onPick }: { currentId?: string; onPick: (id: string) => void }) {
  const { data, isError } = useAgents();
  const feed = useFeedHealth();
  const marks = useProviderMarks();
  const [showOther, setShowOther] = useState(false);
  const pins = usePins();
  const queryClient = useQueryClient();
  useEffect(() => { void loadPinsOnce(); }, []);
  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ['agents'] }); };

  const groups = useMemo(() => {
    // Tolerates BOTH response shapes via the one shared helper — this file used to accept
    // only `{agents:[...]}` and rendered three empty sections on a bare array.
    const rows: RailAgent[] = (agentRowsFrom(data) ?? []).map((a: Record<string, unknown>) => ({
      id: String(a.id),
      name: a.name as string | undefined,
      // The gateway's chip decides the dot and the section when there is one (lib/agentChip.ts
      // railStatus), so a seat waiting on a card it filed needs you here too.
      // With a chip, the chip ALONE decides: its needs_you already counts a pending menu, and the
      // API's own flag could otherwise outrank a chip that says working or down.
      status: railStatus(asChip(a.chip), a.status as string | undefined),
      has_pending_menu: railPendingMenu(asChip(a.chip), !!a.has_pending_menu),
      chip: asChip(a.chip),
      unread: (a.inbox_count as number | undefined) ?? 0,
      provider: a.runtime as string | undefined,
      context_pct: a.context_pct as string | undefined,
      tier: a.tier as string | undefined,
      reports_to: a.reports_to as string | undefined,
      project: (a.project as RailAgent['project']) ?? null,
      display_name: a.display_name as string | undefined,
      full_name: a.full_name as string | undefined,
      last_message_at: (a.last_message_at as string | null | undefined) ?? null,
    }));
    // Name order inside a section; the section itself is the priority signal.
    rows.sort((x, y) => (x.name || x.id).localeCompare(y.name || y.id));
    return groupForRail(rows);
  }, [data]);

  // Every row's live record in the avatar hub: its state (the same reading the dot used), provider and name.
  useEffect(() => {
    for (const a of [...groups.needsYou, ...groups.working, ...groups.idle, ...groups.other] as (RailAgent & { chip?: AgentChip })[]) {
      const status = avatarStateFor(a.status, !!a.has_pending_menu, isDegraded(feed));
      const provider = (a.chip?.provider || a.provider || null)?.toLowerCase() ?? null;
      const prev = avatarHub.get(a.id) ?? ensureAgent(a.id);
      if (prev.status !== status || prev.provider !== provider || prev.name !== (a.name || a.id)) {
        avatarHub.set(a.id, { status, provider, name: a.name || a.id });
      }
    }
  }, [groups, feed]);

  // A column that prints the SAME two letters on every row tells the reader nothing and
  // costs a column of width to do it. The badge earns its place only when it distinguishes
  // one row from another, so it appears when at least two distinct runtimes are on screen.
  // A row with no runtime is not a second value: 138 of 320 live rows carry none, and
  // counting "unknown" as variety would make the badge permanent by accident.
  const badgesVary = useMemo(
    // ON SCREEN means on screen, which is why `other` is conditional rather than dropped.
    // Counting it unconditionally made one retired codex seat among ~299 collapsed rows print
    // "CL" on every visible row — the identical-column noise this rule exists to kill. But
    // dropping it unconditionally is the INVERSE fault: expand "Not running" and two distinct
    // runtimes are on screen with the disambiguating badge suppressed. The predicate has to
    // follow the disclosure, not approximate it.
    () => providersVary([...groups.needsYou, ...groups.working, ...groups.idle,
      ...(showOther ? groups.other : [])]),
    [groups, showOther],
  );

  // One section's entries: project groups (two or more of a project's agents here) and rows.
  const renderItems = (items: RailItem[], section: string, pick: (id: string) => void) => items.map((it) => {
    if (it.kind === 'row') {
      return <RailRow key={it.agent.id} agent={it.agent} active={it.agent.id === currentId} feed={feed} showProvider={badgesVary} marks={marks}
                      label={rowLabel(it.agent, false)} pinnedNow={pins.includes(it.agent.id)} onRenamed={refresh} onPick={pick} />;
    }
    return (
      <ProjectGroup key={`${section}:${it.project.slug}`} section={section} project={it.project} agents={it.agents}
                    defaultOpen={section === 'needsYou'} currentId={currentId} onPick={pick}>
        {it.agents.map((a) => (
          <RailRow key={a.id} agent={a} active={a.id === currentId} feed={feed} showProvider={badgesVary} marks={marks}
                   label={rowLabel(a, true)} pinnedNow={pins.includes(a.id)} onRenamed={refresh} onPick={pick} />
        ))}
      </ProjectGroup>
    );
  });
  const everyone = [...groups.needsYou, ...groups.working, ...groups.idle, ...groups.other];
  const pinnedAgents = pins.map((id) => everyone.find((a) => a.id === id)).filter(Boolean) as RailAgent[];

  // An empty rail and a BROKEN rail must not look the same: a failed feed says so, because
  // "no agents need you" and "we could not ask" are different facts.
  if (isError) {
    return (
      <nav className="w-full p-3 text-sm text-red-600 dark:text-red-400" aria-label="Agents">
        Could not load agents. The rail is not empty — it is unknown.
      </nav>
    );
  }
  // "Loading" is only honest while a first answer is still PLAUSIBLY coming, which is exactly
  // what CONNECTING means: never heard, a fetch in flight, no error yet. The moment that fetch
  // fails or stops the verdict turns disconnected and the banner below takes over, so this
  // cannot become the eternal spinner it replaced.
  //
  // It used to read `isLoading && !data && feed.health !== 'disconnected'`, which could NEVER be
  // true: feedHealthOf returned `disconnected` for any !hasData, so the third clause contradicted
  // the second. Every cold page load fell through and announced "Not connected to the fleet" for
  // the length of the first fetch — a healthy fleet reported as an outage.
  if (feed.health === 'connecting') {
    return <nav className="w-full p-3 text-sm text-neutral-600 dark:text-neutral-500" aria-label="Agents">Loading agents…</nav>;
  }

  return (
    <nav className="w-full flex flex-col gap-3 p-2 shrink-0" aria-label="Agents">
      {/* The whole column says it ONCE, so a reader does not have to infer an outage from every
          row having gone grey. The sections below are the LAST thing we were told, not now. */}
      {isDegraded(feed) && (
        <p className="mx-2 px-2 py-1.5 rounded border border-neutral-300 dark:border-neutral-700 bg-neutral-100 dark:bg-neutral-900 text-[11px] text-muted-foreground" role="status">
          {feed.health === 'disconnected' ? 'Not connected to the fleet.' : 'Connection lost.'}{' '}
          {feed.lastSeenAt ? `Showing what we last saw — ${lastSeenLabel(feed.lastSeenAt)}.` : 'No data has arrived yet.'}
        </p>
      )}
      {/* Pinned faces, under the Agents header and above everything else; one click opens that agent. */}
      {pinnedAgents.length > 0 && (
        <div data-rail-pins className="-mb-1">
          <FaceRow agents={pinnedAgents} currentId={currentId} onPick={onPick} max={24} label="Pinned agents" />
        </div>
      )}
      {SECTIONS.map(({ key, label, badge }) => {
        const rows = groups[key];
        return (
          <section key={key} aria-labelledby={`rail-${key}`}>
            <h2
              id={`rail-${key}`}
              className="flex items-center gap-2 px-2 pb-1 text-[11px] uppercase tracking-wide text-neutral-600 dark:text-neutral-500"
            >
              <span>{label}</span>
              {badge && rows.length > 0 && (
                <span className="px-1.5 rounded-full bg-amber-400/20 text-amber-800 dark:text-amber-300 text-[10px] tabular-nums">
                  {rows.length}
                </span>
              )}
              {!badge && <span className="text-neutral-500 dark:text-neutral-600 tabular-nums">{rows.length}</span>}
            </h2>
            {rows.length === 0 ? (
              <p className="px-2 py-1 text-xs text-neutral-500 dark:text-neutral-600">
                {key === 'needsYou' ? 'Nothing waiting on you.' : `No ${label.toLowerCase()} agents.`}
              </p>
            ) : (
              <div className="flex flex-col">
                {/* From "Needs you", opening the agent also brings its open decision into view (DecisionBubble). */}
                {renderItems(sectionItems(rows), key, key === 'needsYou' ? (id) => { focusDecision(id); onPick(id); } : onPick)}
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
            // stopPropagation for the same reason as the sidebar's "More": this rail renders
            // inside <aside onClick={onNavigate}>, which on mobile closes the drawer. Without
            // it, tapping "Not running" expanded the bucket and shut the drawer in one gesture.
            // The "More" fix in this same commit left its twin standing — fixing one site of a
            // class is not fixing the class.
            onClick={(e) => { e.stopPropagation(); setShowOther((v) => !v); }}
            className="w-full flex items-center gap-2 px-2 py-1 text-[11px] uppercase tracking-wide text-neutral-600 dark:text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300"
            aria-expanded={showOther}
          >
            <span>{showOther ? '▾' : '▸'}</span>
            <span>Not running</span>
            <span className="text-neutral-500 dark:text-neutral-600 tabular-nums">{groups.other.length}</span>
          </button>
          {showOther && (
            <div className="flex flex-col">
              {renderItems(sectionItems(groups.other), 'other', onPick)}
            </div>
          )}
        </section>
      )}
    </nav>
  );
}
