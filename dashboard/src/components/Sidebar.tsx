import { useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate, useMatch } from 'react-router-dom';
import {
  LayoutDashboard,
  Bot,
  ListTodo,
  Activity,
  ShieldCheck,
  Clock,
  BarChart3,
  Radar,
  Sparkles,
  Lightbulb,
  ClipboardList,
  Mic,
  FolderKanban,
  LogOut,
  Inbox,
  MessagesSquare,
  Sparkle,
  ChevronRight,
  ChevronDown,
  Plus,
  Check,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { LucideIcon } from 'lucide-react';
import { useUser } from '../hooks/useUser';
import { ASSISTANT_V2_ENABLED } from '../lib/assistant/config';
import { AgentRail } from './workbench/AgentRail';
import UsageLine from './workbench/UsageLine';
import { listThreads, type ThreadSummary } from '../lib/arturoThreads';
import { currentThread, setCurrentThread, subscribeCurrentThread } from '../lib/arturoCurrent';
import { startNewArturoChat } from '../lib/arturoNewChat';
import { operatorUserId } from '../lib/runtimeConfig';
import { navItemsFor, isViewHidden } from '../lib/features';
import { useTheme } from '../theme/ThemeProvider';
import { AgentAvatar } from './avatar/AgentAvatar';
import { avatarHub, ARTURO_ID, setArturoProvider, defaultLook } from './avatar/agentState';
import { arturoHealth } from '../lib/arturo';
import { arturoProvider } from '../lib/avatarStatus';
import { applyAssistantName, useAssistantName } from '../lib/assistantName';
import { useArturoBrain } from '../stores/arturoBrain';
import { loadSavedLooksOnce } from '../lib/avatarLooks';
import { appearanceRows } from '../lib/theme';

// The agents are the work; the pages are where you go occasionally. "More" remembers whether you
// opened it, so anyone who lives in Tasks or Analytics keeps them visible after one click and the
// default never has to be re-litigated. Reading localStorage can THROW (private windows, blocked
// site data), so it is wrapped — a storage failure must not take the whole nav down with it.
const MORE_KEY = 'orchestra.sidebar.moreOpen';
function readMoreOpen(): boolean {
  try { return localStorage.getItem(MORE_KEY) === '1'; } catch { return false; }
}
function writeMoreOpen(v: boolean): void {
  try { localStorage.setItem(MORE_KEY, v ? '1' : '0'); } catch { /* not worth failing the nav for */ }
}

// Chats remembers its open state the same way, starting closed. Only one of Chats and the agents list
// is open at a time, so the agents list is simply "open unless Chats is": it has no saved state of its
// own, and the agents (the work) are what shows by default.
const CHATS_KEY = 'orchestra.sidebar.chatsOpen';
function readOpen(key: string, fallback: boolean): boolean {
  try { const v = localStorage.getItem(key); return v === null ? fallback : v === '1'; } catch { return fallback; }
}
function writeOpen(key: string, v: boolean): void {
  try { localStorage.setItem(key, v ? '1' : '0'); } catch { /* not worth failing the nav for */ }
}

interface NavItem {
  to: string;
  icon: LucideIcon;
  label: string;
}

// Arturo and Agents are not in this list: Arturo is pinned at the top of the sidebar, and the Agents
// header above the agents list already opens the Agents page.
const coreNav: NavItem[] = [
  { to: '/command-center', icon: Radar, label: 'Command Center' },
  { to: '/overview', icon: LayoutDashboard, label: 'Overview' },
  { to: '/inbox', icon: Inbox, label: 'Inbox' },
  { to: '/tasks', icon: ListTodo, label: 'Tasks' },
  { to: '/activity', icon: Activity, label: 'Activity' },
];

const operationsNav: NavItem[] = [
  { to: '/approvals', icon: ShieldCheck, label: 'Approvals' },
  { to: '/workflows', icon: Clock, label: 'Workflows' },
  { to: '/analytics', icon: BarChart3, label: 'Analytics' },
];

const intelligenceNav: NavItem[] = [
  { to: '/skills', icon: Sparkles, label: 'Skills' },
  { to: '/insights', icon: Lightbulb, label: 'Insights' },
  { to: '/questionnaires', icon: ClipboardList, label: 'Questionnaires' },
];

const projectsNav: NavItem[] = [
  { to: '/projects', icon: FolderKanban, label: 'Projects' },
];

const commsNav: NavItem[] = [
  { to: '/voice', icon: Mic, label: 'Voice' },
  { to: '/chat-history', icon: MessagesSquare, label: 'Chat History' },
];

// V2 assistant (B1) — only present when the feature flag is built in.
const assistantNav: NavItem[] = ASSISTANT_V2_ENABLED
  ? [{ to: '/assistant', icon: Sparkle, label: 'Assistant' }]
  : [];

function NavSection({ label, items }: { label: string; items: NavItem[] }) {
  if (items.length === 0) return null;
  return (<><SectionHeader label={label} /><NavItems items={items} /></>);
}

function NavItems({ items }: { items: NavItem[] }) {
  return (
    <>
      {items.map(item => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/'}
          className={({ isActive }) =>
            clsx(
              'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
              isActive
                ? 'bg-neutral-200 dark:bg-neutral-800 text-neutral-950 dark:text-white font-medium'
                : 'text-muted-foreground hover:bg-neutral-100 dark:hover:bg-neutral-900 hover:text-neutral-800 dark:hover:text-neutral-200',
            )
          }
        >
          <item.icon size={16} />
          {item.label}
        </NavLink>
      ))}
    </>
  );
}

/** The header of a collapsible sidebar section (Chats, Agents, More): one size, one column, one style,
 *  so the three read as siblings. The chevron sits in the nav icons' column and the label starts where
 *  the chat titles do. stopPropagation because the <aside> carries onClick={onNavigate}, which on a
 *  phone CLOSES the drawer: expanding is not navigating. */
function SectionToggle({ label, open, onToggle }: { label: string; open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
      aria-expanded={open}
      className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-muted-foreground hover:bg-neutral-100 dark:hover:bg-neutral-900 hover:text-neutral-800 dark:hover:text-neutral-200 transition-colors"
    >
      {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      {label}
    </button>
  );
}

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="px-3 pt-5 pb-1 text-xs font-medium uppercase text-neutral-500 dark:text-neutral-600 tracking-wider">
      {label}
    </div>
  );
}

/** Arturo's conversations, one line each. The same list and the same switch as Arturo's own drawer:
 *  picking one makes it the current thread (the home follows it) and opens the home. */
function ChatList({ onOpen }: { onOpen: (id: string) => void }) {
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [current, setCurrent] = useState<string | null>(currentThread);
  useEffect(() => {
    let live = true;
    const load = () => { void listThreads().then((t) => { if (live) setThreads(t); }); };
    load();
    // A switch elsewhere (the home, the pane, another tab) moves the highlight, and a brand-new
    // thread only appears in the list after a reload of it.
    const off = subscribeCurrentThread((id) => { setCurrent(id); load(); });
    return () => { live = false; off(); };
  }, []);
  // The list is inset so each title starts in the section labels' column; the 8px inside a row is just
  // enough for the current chat's highlight to read around its title.
  return (
    <div className="pl-7">
      {threads === null ? (
        <p className="px-2 py-1 text-xs text-neutral-500 dark:text-neutral-600">Loading…</p>
      ) : threads.length === 0 ? (
        <p className="px-2 py-1 text-xs text-neutral-500 dark:text-neutral-600">No conversations yet.</p>
      ) : (
        // Rows are a fixed 24px, so 240px shows ten and the rest scroll.
        <div className="max-h-60 overflow-y-auto">
          {threads.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => { setCurrent(t.id); onOpen(t.id); }}
              title={t.title || 'Untitled'}
              className={clsx(
                'w-full h-6 flex items-center px-2 rounded text-xs text-left truncate transition-colors',
                t.id === current ? 'text-neutral-900 dark:text-neutral-100 bg-neutral-100 dark:bg-neutral-900' : 'text-muted-foreground hover:bg-neutral-100 dark:hover:bg-neutral-900 hover:text-neutral-800 dark:hover:text-neutral-200',
              )}
            >
              <span className="truncate">{t.title || 'Untitled'}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** The Agents header: the section headers' shape, with the nav items' active look. */
function agentsHeaderClass(active: boolean): string {
  return clsx(
    'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors',
    active ? 'bg-neutral-200 dark:bg-neutral-800 text-neutral-950 dark:text-white font-medium' : 'text-muted-foreground hover:bg-neutral-100 dark:hover:bg-neutral-900 hover:text-neutral-800 dark:hover:text-neutral-200',
  );
}

interface SidebarProps {
  onNavigate?: () => void;
}

export function Sidebar({ onNavigate }: SidebarProps = {}) {
  const { data: user } = useUser();
  const navigate = useNavigate();
  const agentMatch = useMatch('/agent/:id');
  const [moreOpen, setMoreOpen] = useState(readMoreOpen);
  const [chatsOpen, setChatsOpen] = useState(() => readOpen(CHATS_KEY, false));
  const agentsOpen = !chatsOpen;
  const setChats = (v: boolean) => { setChatsOpen(v); writeOpen(CHATS_KEY, v); };
  const username = user?.username || operatorUserId();
  const isAdmin = !user || user.role === 'admin';
  // Every agent's saved avatar look, once per page (the sidebar is on every page).
  useEffect(() => { void loadSavedLooksOnce(avatarHub, defaultLook); }, []);
  // Arturo's badge from its actual brain, on every page (its own page also updates it as health changes).
  const brainChoice = useArturoBrain((s) => s.choice);
  const aname = useAssistantName();
  useEffect(() => {
    let live = true;
    arturoHealth().then((h) => {
      if (!live) return;
      if (h.brain) setArturoProvider(arturoProvider(h.brain, brainChoice));
      if (h.ok) applyAssistantName(h.assistant_name);
    }).catch(() => {});
    return () => { live = false; };
  }, [brainChoice]);

  return (
    <aside className="w-60 border-r border-neutral-200 dark:border-neutral-800 h-full max-h-screen flex flex-col bg-background overflow-hidden" onClick={onNavigate}>
      {/* As tall as the page's top bar beside it (53px: a 40px row, 6px above and below, its border), so the two
          bottom borders read as one line across the screen. */}
      <div className="px-5 py-1.5 min-h-[53px] flex flex-col justify-center border-b border-neutral-200 dark:border-neutral-800 shrink-0">
        <h1 className="text-lg font-bold tracking-tight text-neutral-950 dark:text-white">orchestraOS</h1>
        {/* The subtitle appears only when it SAYS something. "agent command center" was a
            tagline under the product's own name: constant on every screen, for every admin,
            forever — so it carried no information and spent the top of the nav column, the
            one place the agent rail is short of, to carry none. The operator variant is
            different in kind: it names WHOSE workspace this is, which is a fact that varies
            and can be got wrong, so it stays. Same rule as the runtime badge and the siren. */}
        {!isAdmin && (
          <p className="text-xs text-neutral-600 dark:text-neutral-500 mt-0.5">{`${username}'s workspace`}</p>
        )}
      </div>
      {/* Arturo and its chats are pinned under the title, outside the scrolling column, so the way
          home never scrolls away. Taps here bubble to the aside and close the mobile drawer, as
          navigating should; the chevrons stop propagation, as expanding is not navigating. */}
      <div className="px-3 pt-3 pb-1 shrink-0">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            clsx(
              'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
              isActive
                ? 'bg-neutral-200 dark:bg-neutral-800 text-neutral-950 dark:text-white font-medium'
                : 'text-muted-foreground hover:bg-neutral-100 dark:hover:bg-neutral-900 hover:text-neutral-800 dark:hover:text-neutral-200',
            )
          }
        >
          {({ isActive }) => (
            <>
              {/* 32px in a 16px slot (the negative margins): the row and its label keep their place. */}
              <span className={clsx('-mx-2 -my-1.5 shrink-0', isActive ? 'la-on-selected' : 'la-on-sidebar')}>
                <AgentAvatar agentId={ARTURO_ID} role="sidebar" size={32} selected={isActive} label={aname} />
              </span>
              {aname}
            </>
          )}
        </NavLink>
        {/* The plus is a SIBLING laid over the header's right end, not inside it (a button cannot hold a
            button), so it never toggles Chats; it shows whether Chats is open or not. It navigates, so its
            tap bubbles to the aside and closes a phone's drawer like picking a chat. On a phone it is 44px
            wide and as tall as the 44px row, a full tap target without making the row taller. */}
        <div className="relative">
          <SectionToggle label="Chats" open={chatsOpen} onToggle={() => setChats(!chatsOpen)} />
          <button
            type="button"
            aria-label="New chat"
            title="New chat"
            onClick={() => { startNewArturoChat(); navigate('/'); }}
            className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center justify-center p-1.5 max-md:w-11 rounded-md text-muted-foreground hover:bg-neutral-100 dark:hover:bg-neutral-900 hover:text-neutral-800 dark:hover:text-neutral-200 transition-colors"
          >
            <Plus size={16} />
          </button>
        </div>
        {chatsOpen && <ChatList onOpen={(id) => { setCurrentThread(id); navigate('/'); }} />}
      </div>
      {/* ONE column, agents first (PLAN C7). The rail used to be a SECOND column beside this one
          on the agent page, which read as two menus; it now lives here and the page has none. */}
      <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
        <div className="shrink-0 border-t border-neutral-200 dark:border-neutral-800">
          <div className="px-3 pt-3">
            {/* A nav item, not a toggle: it opens the Agents page and brings the agents list back (closing
                Chats), never hides it. Its icon sits in the section chevrons' column, its label with
                theirs. The tap navigates, so on a phone it closes the drawer like any nav item. Where the
                deployment hides the Agents page, it only brings the list back (and, not navigating, it
                leaves a phone's drawer open). */}
            {!isViewHidden('/agents') ? (
              <NavLink
                to="/agents"
                onClick={() => setChats(false)}
                className={({ isActive }) => agentsHeaderClass(isActive)}
              >
                <Bot size={16} />
                Agents
              </NavLink>
            ) : (
              <button type="button" onClick={(e) => { e.stopPropagation(); setChats(false); }} className={agentsHeaderClass(false)}>
                <Bot size={16} />
                Agents
              </button>
            )}
          </div>
          {agentsOpen && (
            <AgentRail
              currentId={agentMatch?.params.id}
              onPick={(next) => { navigate(`/agent/${next}`); onNavigate?.(); }}
            />
          )}
        </div>

        {/* shrink-0 on BOTH children, because the parent is the single scroll container.
            Without it, expanding "More" grew this nav inside a `flex flex-col` and flex took
            the space back out of the rail — which carries overflow-y-auto, so its min-height
            collapsed and the agents list became a ~16px strip at 1440x900 (harness-ux-planner,
            shots 03/03b on public main 056474f). Neither child shrinks now; the column scrolls. */}
        <nav className="p-3 space-y-0.5 border-t border-neutral-200 dark:border-neutral-800 shrink-0">
          {/* SectionToggle stops propagation: without it, tapping More on a phone expanded the section and
              shut the drawer in the same gesture, so Tasks/Analytics/Comms were never seen. */}
          <SectionToggle label="More" open={moreOpen} onToggle={() => { const v = !moreOpen; setMoreOpen(v); writeMoreOpen(v); }} />
          {moreOpen && (
            <>
              {/* Every list goes through navItemsFor (features + hiddenViews), and a section whose
                  every entry is hidden loses its header too. */}
              <NavItems items={navItemsFor(assistantNav)} />
              <NavItems items={navItemsFor(coreNav)} />
              <NavSection label="Operations" items={navItemsFor(operationsNav)} />
              <NavSection label="Intelligence" items={navItemsFor(intelligenceNav)} />
              <NavSection label="Projects" items={navItemsFor(projectsNav)} />
              <NavSection label="Comms" items={navItemsFor(commsNav)} />
            </>
          )}
        </nav>
      </div>

      {/* User card. Its name and role open the operator menu (Appearance, per device); the logout icon is
          its own button beside it, as before. */}
      <div className="p-3 border-t border-neutral-200 dark:border-neutral-800 shrink-0">
        <OperatorCard username={username} role={isAdmin ? 'admin' : user?.role || 'user'} />
        <div className="pt-2">
          <UsageLine />
        </div>
      </div>
    </aside>
  );
}

/** The operator card and its menu. The menu opens above the card, inside the sidebar, so it stays on screen
 *  in the phone drawer too; its rows are 44px there. Clicks inside never reach the sidebar's own handler,
 *  which would close the phone drawer. */
function OperatorCard({ username, role }: { username: string; role: string }) {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (!wrapRef.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); buttonRef.current?.focus(); } };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative" onClick={(e) => e.stopPropagation()}>
      {open && (
        <div role="menu" aria-label="Operator" data-operator-menu
             className="absolute bottom-full left-0 right-0 mb-2 z-10 rounded-xl border border-border bg-popover text-popover-foreground shadow-lg py-1">
          <p className="px-3 pt-1.5 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Appearance</p>
          {appearanceRows(theme).map((o) => (
            <button key={o.id} type="button" role="menuitemradio" aria-checked={o.checked}
                    onClick={() => { setTheme(o.id); setOpen(false); buttonRef.current?.focus(); }}
                    className="w-full min-h-9 max-md:min-h-11 flex items-center justify-between gap-2 px-3 text-sm text-left hover:bg-muted transition-colors">
              <span>{o.label}</span>
              {o.checked && <Check size={16} className="text-foreground" aria-hidden />}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-neutral-100 dark:bg-neutral-900">
        {/* The button spans the card's full height (the -my-2 py-2 pair) so it is a finger-sized target
            without making the card any taller. */}
        <button ref={buttonRef} type="button" data-operator-card aria-haspopup="menu" aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
                className="flex-1 min-w-0 -my-2 py-2 flex items-center gap-3 text-left rounded-lg">
          <div className="w-8 h-8 rounded-full bg-violet-600 flex items-center justify-center text-xs font-bold text-white shrink-0">
            {username[0].toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-neutral-800 dark:text-neutral-200 truncate">{username}</p>
            <p className="text-[10px] text-neutral-600 dark:text-neutral-500">{role}</p>
          </div>
        </button>
        <button
          onClick={() => { window.location.href = '/logout'; }}
          className="p-1.5 text-neutral-600 dark:text-neutral-500 hover:text-red-600 dark:hover:text-red-400 hover:bg-neutral-200 dark:hover:bg-neutral-800 rounded-lg transition-colors"
          title="Logout"
        >
          <LogOut size={14} />
        </button>
      </div>
    </div>
  );
}
