import { useState } from 'react';
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
} from 'lucide-react';
import { clsx } from 'clsx';
import type { LucideIcon } from 'lucide-react';
import { useUser } from '../hooks/useUser';
import { ASSISTANT_V2_ENABLED } from '../lib/assistant/config';
import { AgentRail } from './workbench/AgentRail';
import { operatorUserId } from '../lib/runtimeConfig';
import { navItemsFor } from '../lib/features';

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

interface NavItem {
  to: string;
  icon: LucideIcon;
  label: string;
}

const coreNav: NavItem[] = [
  { to: '/', icon: Sparkle, label: 'Arturo' },
  { to: '/command-center', icon: Radar, label: 'Command Center' },
  { to: '/overview', icon: LayoutDashboard, label: 'Overview' },
  { to: '/inbox', icon: Inbox, label: 'Inbox' },
  { to: '/agents', icon: Bot, label: 'Agents' },
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
                ? 'bg-neutral-800 text-white font-medium'
                : 'text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200',
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

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="px-3 pt-5 pb-1 text-xs font-medium uppercase text-neutral-600 tracking-wider">
      {label}
    </div>
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
  const username = user?.username || operatorUserId();
  const isAdmin = !user || user.role === 'admin';

  return (
    <aside className="w-60 border-r border-neutral-800 h-full max-h-screen flex flex-col bg-neutral-950 overflow-hidden" onClick={onNavigate}>
      <div className="p-5 border-b border-neutral-800 shrink-0">
        <h1 className="text-lg font-bold tracking-tight text-white">orchestraOS</h1>
        {/* The subtitle appears only when it SAYS something. "agent command center" was a
            tagline under the product's own name: constant on every screen, for every admin,
            forever — so it carried no information and spent the top of the nav column, the
            one place the agent rail is short of, to carry none. The operator variant is
            different in kind: it names WHOSE workspace this is, which is a fact that varies
            and can be got wrong, so it stays. Same rule as the runtime badge and the siren. */}
        {!isAdmin && (
          <p className="text-xs text-neutral-500 mt-0.5">{`${username}'s workspace`}</p>
        )}
      </div>
      {/* ONE column, agents first (PLAN C7). The rail used to be a SECOND column beside this one
          on the agent page, which read as two menus; it now lives here and the page has none. */}
      <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
        <AgentRail
          currentId={agentMatch?.params.id}
          onPick={(next) => { navigate(`/agent/${next}`); onNavigate?.(); }}
        />

        {/* shrink-0 on BOTH children, because the parent is the single scroll container.
            Without it, expanding "More" grew this nav inside a `flex flex-col` and flex took
            the space back out of the rail — which carries overflow-y-auto, so its min-height
            collapsed and the agents list became a ~16px strip at 1440x900 (harness-ux-planner,
            shots 03/03b on public main 056474f). Neither child shrinks now; the column scrolls. */}
        <nav className="p-3 space-y-0.5 border-t border-neutral-800 shrink-0">
          <button
            type="button"
            // stopPropagation because the <aside> above carries onClick={onNavigate}, which on
            // mobile CLOSES the drawer. Without it, tapping More expanded the section and shut
            // the drawer in the same gesture: the operator never saw Tasks/Analytics/Comms and
            // the control read as broken. Expanding is not navigating.
            onClick={(e) => { e.stopPropagation(); const v = !moreOpen; setMoreOpen(v); writeMoreOpen(v); }}
            aria-expanded={moreOpen}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200 transition-colors"
          >
            {moreOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            More
          </button>
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

      {/* User card */}
      <div className="p-3 border-t border-neutral-800 shrink-0">
        <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-neutral-900">
          <div className="w-8 h-8 rounded-full bg-violet-600 flex items-center justify-center text-xs font-bold text-white shrink-0">
            {username[0].toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-neutral-200 truncate">{username}</p>
            <p className="text-[10px] text-neutral-500">{isAdmin ? 'admin' : user?.role || 'user'}</p>
          </div>
          <button
            onClick={() => { window.location.href = '/logout'; }}
            className="p-1.5 text-neutral-500 hover:text-red-400 hover:bg-neutral-800 rounded-lg transition-colors"
            title="Logout"
          >
            <LogOut size={14} />
          </button>
        </div>
      </div>
    </aside>
  );
}
