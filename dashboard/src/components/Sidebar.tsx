import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  Bot,
  ListTodo,
  Activity,
  Map,
  ShieldCheck,
  Clock,
  BarChart3,
  Radar,
  FlaskConical,
  Sparkles,
  Lightbulb,
  ClipboardList,
  Mic,
  Building2,
  FolderKanban,
  LogOut,
  Inbox,
  Brain,
  Contact,
  Megaphone,
  MessagesSquare,
  Sparkle,
} from 'lucide-react';
import { clsx } from 'clsx';
import type { LucideIcon } from 'lucide-react';
import { useUser } from '../hooks/useUser';
import { ASSISTANT_V2_ENABLED } from '../lib/assistant/config';

interface NavItem {
  to: string;
  icon: LucideIcon;
  label: string;
}

const coreNav: NavItem[] = [
  { to: '/command-center', icon: Radar, label: 'Command Center' },
  { to: '/', icon: LayoutDashboard, label: 'Overview' },
  { to: '/inbox', icon: Inbox, label: 'Inbox' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/tasks', icon: ListTodo, label: 'Tasks' },
  { to: '/activity', icon: Activity, label: 'Activity' },
];

const operationsNav: NavItem[] = [
  { to: '/strategy', icon: Map, label: 'Strategy' },
  { to: '/approvals', icon: ShieldCheck, label: 'Approvals' },
  { to: '/workflows', icon: Clock, label: 'Workflows' },
  { to: '/analytics', icon: BarChart3, label: 'Analytics' },
];

const intelligenceNav: NavItem[] = [
  { to: '/learning', icon: Brain, label: 'Learning' },
  { to: '/experiments', icon: FlaskConical, label: 'Experiments' },
  { to: '/skills', icon: Sparkles, label: 'Skills' },
  { to: '/insights', icon: Lightbulb, label: 'Insights' },
  { to: '/questionnaires', icon: ClipboardList, label: 'Questionnaires' },
];

const clientsNav: NavItem[] = [
  { to: '/clients', icon: Building2, label: 'Clients' },
  { to: '/people', icon: Contact, label: 'People' },
  { to: '/projects', icon: FolderKanban, label: 'Projects' },
  { to: '/campaigns', icon: Megaphone, label: 'Campaigns' },
];

const commsNav: NavItem[] = [
  { to: '/voice', icon: Mic, label: 'Voice' },
  { to: '/chat-history', icon: MessagesSquare, label: 'Chat History' },
];

// V2 assistant (B1) — only present when the feature flag is built in.
const assistantNav: NavItem[] = ASSISTANT_V2_ENABLED
  ? [{ to: '/assistant', icon: Sparkle, label: 'Assistant' }]
  : [];

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
  const username = user?.username || 'operator';
  const isAdmin = !user || user.role === 'admin';

  return (
    <aside className="w-60 border-r border-neutral-800 h-full max-h-screen flex flex-col bg-neutral-950 overflow-hidden" onClick={onNavigate}>
      <div className="p-5 border-b border-neutral-800 shrink-0">
        <h1 className="text-lg font-bold tracking-tight text-white">orchestraOS</h1>
        <p className="text-xs text-neutral-500 mt-0.5">
          {isAdmin ? 'agent command center' : `${username}'s workspace`}
        </p>
      </div>
      <nav className="flex-1 p-3 space-y-0.5 overflow-y-auto min-h-0">
        {assistantNav.length > 0 && <NavItems items={assistantNav} />}
        <NavItems items={coreNav} />
        <SectionHeader label="Operations" />
        <NavItems items={operationsNav} />
        <SectionHeader label="Intelligence" />
        <NavItems items={intelligenceNav} />
        {isAdmin && (
          <>
            <SectionHeader label="Clients" />
            <NavItems items={clientsNav} />
          </>
        )}
        <SectionHeader label="Comms" />
        <NavItems items={commsNav} />
      </nav>

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
