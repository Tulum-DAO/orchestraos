import { useEffect, useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAgents } from '../hooks/useAgents';
import { useTasks } from '../hooks/useTasks';
import { useSystem } from '../hooks/useSystem';
import { useTransit, useGoDark, useReturn } from '../hooks/useTransit';
import { fetchActivity, fetchContext } from '../lib/api';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { StatCard } from '../components/StatCard';
import { StatusDot } from '../components/StatusDot';
import { TierBadge } from '../components/TierBadge';
import { LiveFeed } from '../components/LiveFeed';
import { FleetRecoveryModal } from '../components/FleetRecoveryModal';
import NewAgentModal from '../components/NewAgentModal';
import { useQueryClient } from '@tanstack/react-query';

const TIER_ORDER: Record<string, number> = { T0: 0, T1: 1, T2: 2, T3: 3 };

export default function Overview() {
  const { data: agentsData, isLoading: agentsLoading } = useAgents();
  const { data: tasksData, isLoading: tasksLoading } = useTasks();
  const { data: systemData } = useSystem();
  const { data: transitData } = useTransit();
  const goDarkMutation = useGoDark();
  const returnMutation = useReturn();
  const [showConfirm, setShowConfirm] = useState(false);
  const [showNewAgent, setShowNewAgent] = useState(false);
  const queryClient = useQueryClient();
  const [showReport, setShowReport] = useState(false);
  const [transitReport, setTransitReport] = useState<any>(null);
  const { data: context } = useQuery({
    queryKey: ['context'],
    queryFn: fetchContext,
  });
  // /api/context/needs-attention does not exist server-side (404 on every
  // load, 2026-08-19). The render below is gated on items.length > 0, so the
  // failure used to be INVISIBLE: a dead feed and "nothing needs you" looked
  // identical to the operator. Now the error is surfaced (see the Needs You block) —
  // an error he can see beats a blank he cannot distinguish from calm.
  // Deliberately not "fixed" by adding the endpoint: what needs the operator is
  // already answered by three disagreeing stores, and inventing a fourth
  // server-side answer would deepen that, not resolve it. Missing endpoint
  // reported to gm for the pipeline owners.
  const {
    data: attentionData,
    isError: attentionFailed,
  } = useQuery({
    queryKey: ['needs-attention'],
    queryFn: async () => {
      const r = await fetch('/api/context/needs-attention');
      if (!r.ok) throw new Error(`needs-attention ${r.status}`);
      return r.json();
    },
    retry: false,
    refetchInterval: 30_000,
  });
  const attentionItems: any[] =
    (Array.isArray(attentionData) ? attentionData : attentionData?.items) || [];
  // SAME FEED AS THE INBOX PAGE (2026-08-19). The Approvals card was a literal
  // `value={0}` with sub "Queue clear" — wired to nothing, so it read "queue
  // clear" no matter how many decisions were actually waiting on the operator. It now
  // reads exactly what /inbox reads, so the headline number and the page it
  // sends him to cannot disagree. Deliberately NOT a fourth count of its own:
  // "what is waiting on the operator" is currently answered by three different stores
  // (this unified feed, the HTML-questionnaire index, and the native tasks.db
  // rows behind the watch). Picking a winner is a pipeline decision; matching
  // /inbox is the part this surface owns.
  const { data: unifiedApprovals } = useQuery({
    queryKey: ['approvals-unified'],
    queryFn: () => fetch('/api/approvals/unified').then((r) => r.json()),
    refetchInterval: 10_000,
  });
  const approvalsPending = (unifiedApprovals?.items || []).filter(
    (i: any) => i?.status === 'pending',
  ).length;
  const addActivity = useOrchestraStore((s) => s.addActivity);
  const activity = useOrchestraStore((s) => s.activity);

  const handleGoDark = useCallback(() => {
    goDarkMutation.mutate(undefined, {
      onSuccess: () => setShowConfirm(false),
    });
  }, [goDarkMutation]);

  const handleReturn = useCallback(() => {
    returnMutation.mutate(undefined, {
      onSuccess: (data: any) => {
        setTransitReport(data?.report || null);
        setShowReport(true);
      },
    });
  }, [returnMutation]);

  // Seed activity store with initial data on mount
  useEffect(() => {
    if (activity.length > 0) return;
    fetchActivity(50).then((data: any) => {
      const events = data?.events || data?.activity || [];
      events.forEach((e: any) => addActivity(e));
    }).catch(() => {});
  }, []);

  if (agentsLoading || tasksLoading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-neutral-100 mb-6">Overview</h1>
        <p className="text-neutral-500">Loading...</p>
      </div>
    );
  }

  const agents = agentsData?.agents || [];
  const totalAgents = agentsData?.total || 0;
  const onlineAgents = agentsData?.online || 0;

  const summary = tasksData?.summary || {};
  const tasksToday = summary.today || 0;
  const activeCount = summary.active || 0;
  const pendingCount = summary.pending || 0;
  const blockedCount = summary.blocked || 0;

  // Completed tasks today — filter from tasks list
  const completedTasks: any[] = (tasksData?.tasks || []).filter((t: any) =>
    (t.status === 'completed' || t.status === 'complete' || t.status === 'done') &&
    t.completed_date && new Date(t.completed_date).toDateString() === new Date().toDateString()
  );

  // Sort agents: T0 first, then T1, T2, T3
  const sortedAgents = [...agents]
    .sort((a: any, b: any) => (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9))
    .slice(0, 12);

  const isTransitActive = transitData?.active === true;

  return (
    <div className="p-6 space-y-6">
      {/* Transit Banner — shown when transit is active */}
      {isTransitActive && (
        <TransitBanner
          startedAt={transitData.started_at}
          taskCount={transitData.tasks_continued?.length || 0}
          onReturn={handleReturn}
          returning={returnMutation.isPending}
        />
      )}

      {/* Confirmation Dialog */}
      {showConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="rounded-xl border border-neutral-700 bg-neutral-900 p-6 max-w-md w-full shadow-2xl">
            <h3 className="text-lg font-semibold text-neutral-100 mb-2">Going Dark</h3>
            <p className="text-sm text-neutral-400 mb-4">
              Snapshot Mac agents and switch to VPS-only mode? The VPS will continue work autonomously while you're away.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 text-sm rounded-lg bg-neutral-800 text-neutral-300 hover:bg-neutral-700 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleGoDark}
                disabled={goDarkMutation.isPending}
                className="px-4 py-2 text-sm rounded-lg bg-amber-600 text-white hover:bg-amber-500 transition-colors disabled:opacity-50"
              >
                {goDarkMutation.isPending ? 'Activating...' : 'Go Dark'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Return Report Modal */}
      {showReport && transitReport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="rounded-xl border border-neutral-700 bg-neutral-900 p-6 max-w-md w-full shadow-2xl">
            <h3 className="text-lg font-semibold text-neutral-100 mb-3">Transit Report</h3>
            <div className="space-y-2 text-sm mb-4">
              <div className="flex justify-between text-neutral-300">
                <span className="text-neutral-500">Duration</span>
                <span>{transitReport.duration_minutes} minutes</span>
              </div>
              <div className="flex justify-between text-neutral-300">
                <span className="text-neutral-500">Agents recovered</span>
                <span>{transitReport.mac_agents_recovered}</span>
              </div>
              <div className="flex justify-between text-neutral-300">
                <span className="text-neutral-500">Tasks continued</span>
                <span>{transitReport.tasks_continued}</span>
              </div>
              <div className="flex justify-between text-neutral-300">
                <span className="text-neutral-500">Tasks completed</span>
                <span className="text-green-400">{transitReport.tasks_completed}</span>
              </div>
              {transitReport.summary && (
                <p className="text-neutral-400 mt-2 pt-2 border-t border-neutral-800">
                  {transitReport.summary}
                </p>
              )}
            </div>
            <div className="flex justify-end">
              <button
                onClick={() => setShowReport(false)}
                className="px-4 py-2 text-sm rounded-lg bg-neutral-800 text-neutral-300 hover:bg-neutral-700 transition-colors"
              >
                Got it
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Header with the New Agent button (it replaced Going Dark, which assumed a
          second machine and a transit flow most installs do not have; the go-dark
          endpoints and the transit banner above are untouched). */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-neutral-100">Overview</h1>
        <button
          onClick={() => setShowNewAgent(true)}
          className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-blue-900/50 border border-blue-700/50 text-blue-200 hover:bg-blue-800/60 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path strokeLinecap="round" d="M12 5v14M5 12h14" />
          </svg>
          New Agent
        </button>
      </div>

      <NewAgentModal
        open={showNewAgent}
        onClose={() => setShowNewAgent(false)}
        taken={new Set(agents.map((a: { id: string }) => String(a.id)))}
        onCreated={() => { queryClient.invalidateQueries({ queryKey: ['agents'] }); }}
      />

      {/* Needs You — a dead feed must not render as calm */}
      {attentionFailed && (
        <div className="mb-6">
          <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider mb-3">Needs You</h2>
          <div className="p-3 rounded-lg bg-neutral-800/50 border-l-2 border-red-500">
            <div className="text-sm text-red-300">Attention feed unavailable</div>
            <div className="text-xs text-neutral-400 mt-1">
              <code>/api/context/needs-attention</code> is not responding — this panel is
              NOT saying nothing needs you. Check the Inbox and Approvals pages directly.
            </div>
          </div>
        </div>
      )}
      {!attentionFailed && attentionItems.length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider mb-3">Needs You</h2>
          <div className="space-y-2">
            {attentionItems.map((item: any, i: number) => (
              <div
                key={i}
                className={`p-3 rounded-lg bg-neutral-800/50 border-l-2 ${
                  item.type === 'messages'
                    ? 'border-blue-500'
                    : item.type === 'stale'
                      ? 'border-amber-500'
                      : 'border-purple-500'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                      item.type === 'messages'
                        ? 'bg-blue-500/20 text-blue-400'
                        : item.type === 'stale'
                          ? 'bg-amber-500/20 text-amber-400'
                          : 'bg-purple-500/20 text-purple-400'
                    }`}
                  >
                    {item.type}
                  </span>
                  <span className="text-sm text-neutral-200">{item.label}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stat Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Agents Online" value={`${onlineAgents}/${totalAgents}`} />
        <StatCard label="Tasks Today" value={tasksToday} sub={`${activeCount} active, ${pendingCount} queued`} />
        {/* The sub must NOT imply totality (gm ruling 2026-08-19). This counts
            ONE of four stores that answer "what awaits the operator"; questionnaire
            forms live in a different store and are not in this number, so
            "Waiting on you" / "Queue clear" would be the hardcoded zero's bug
            in miniature — a true number implying a false whole. Scoped the
            label rather than summing two stores, because merging them in a
            stat card would be a client quietly making a fleet-architecture
            decision. See docs/MAP_approval-stores-reconciliation.md. */}
        <StatCard
          label="Approvals"
          value={approvalsPending}
          alert={approvalsPending > 0}
          sub="Approvals lane only — forms in Inbox"
        />
        {/* sub was "Waiting for your input", which reads as "no decision awaits
            you" — but this counts BLOCKED TASKS (summary.blocked), a different
            thing from the approvals/questionnaires that actually wait on the operator.
            Labelled for what it measures. NOTE: the v2 tasks summary currently
            emits no `blocked` key at all, so this renders 0 unconditionally —
            reported, not papered over with an invented count. */}
        <StatCard
          label="Attention Required"
          value={blockedCount}
          alert={blockedCount > 0}
          sub="Blocked tasks"
        />
      </div>

      {/* Machine Status */}
      <MachineStatusSection system={systemData} />

      {/* Two-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Agent Fleet Table */}
        <div className="lg:col-span-2 rounded-xl border border-neutral-800 bg-neutral-900 p-4 overflow-x-auto">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider">Agent Fleet</h2>
            <FleetRecoveryModal />
          </div>
          <table className="w-full text-sm min-w-[500px]">
            <thead>
              <tr className="text-neutral-500 text-xs uppercase tracking-wider">
                <th className="text-left pb-2 font-medium">Agent</th>
                <th className="text-left pb-2 font-medium">Tier</th>
                <th className="text-left pb-2 font-medium">Machine</th>
                <th className="text-left pb-2 font-medium">Task</th>
              </tr>
            </thead>
            <tbody>
              {sortedAgents.map((agent: any) => (
                <tr key={agent.id} className="border-t border-neutral-800/50 hover:bg-neutral-800/30">
                  <td className="py-2 flex items-center gap-2">
                    <StatusDot status={agent.alive ? 'running' : 'stopped'} />
                    <span className="text-neutral-100">{agent.name}</span>
                  </td>
                  <td className="py-2"><TierBadge tier={agent.tier} /></td>
                  <td className="py-2 text-neutral-400">{agent.machine || '\u2014'}</td>
                  <td className="py-2 text-neutral-500 truncate max-w-[200px]">
                    {agent.current_task || agent.state || '\u2014'}
                  </td>
                </tr>
              ))}
              {sortedAgents.length === 0 && (
                <tr><td colSpan={4} className="py-4 text-neutral-600 text-center">No agents registered</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Live Activity Feed */}
        <div className="lg:col-span-1 rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <h2 className="text-sm font-semibold text-neutral-400 uppercase tracking-wider mb-3">Live Activity</h2>
          <div className="max-h-96 overflow-y-auto">
            <LiveFeed limit={30} />
          </div>
        </div>
      </div>

      {/* Bottom sections: Current Focus + Today's Progress */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        {/* Current Focus */}
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 border-l-4 border-l-amber-500/70">
          <h2 className="text-xs font-semibold text-amber-400/70 uppercase tracking-wider mb-2">
            Current Focus
          </h2>
          {context?.top_of_mind ? (
            <p className="text-sm text-neutral-300 leading-relaxed whitespace-pre-wrap">
              {context.top_of_mind}
            </p>
          ) : (
            <p className="text-sm text-neutral-600 italic">No focus set — update on the Strategy page</p>
          )}
        </div>

        {/* Today's Progress */}
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <h2 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2">
            Today's Progress
          </h2>
          {completedTasks.length > 0 ? (
            <div className="space-y-2">
              <p className="text-sm text-green-400">
                {completedTasks.length} task{completedTasks.length !== 1 ? 's' : ''} completed today
              </p>
              <div className="space-y-1.5 mt-2">
                {completedTasks.slice(0, 8).map((task: any, i: number) => (
                  <div key={task.id || i} className="flex items-center gap-2 text-xs">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 shrink-0" />
                    <span className="text-neutral-300 truncate">{task.name || task.title || 'Untitled task'}</span>
                    {task.agent && (
                      <span className="ml-auto text-neutral-600 shrink-0">{task.agent}</span>
                    )}
                  </div>
                ))}
                {completedTasks.length > 8 && (
                  <p className="text-xs text-neutral-600 mt-1">
                    +{completedTasks.length - 8} more
                  </p>
                )}
              </div>
            </div>
          ) : tasksToday > 0 ? (
            <p className="text-sm text-neutral-500">
              {tasksToday} task{tasksToday !== 1 ? 's' : ''} in progress — no completions yet today
            </p>
          ) : (
            <p className="text-sm text-neutral-600">No completions yet today</p>
          )}
        </div>
      </div>
    </div>
  );
}

function relativeTime(ts: string | undefined): string {
  if (!ts) return 'unknown';
  const diff = Date.now() - new Date(ts).getTime();
  if (diff < 0) return 'just now';
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const STATUS_DOT: Record<string, string> = {
  online: 'bg-green-500',
  sleeping: 'bg-amber-500',
  offline: 'bg-red-500',
};

function TransitBanner({
  startedAt,
  taskCount,
  onReturn,
  returning,
}: {
  startedAt: string | null;
  taskCount: number;
  onReturn: () => void;
  returning: boolean;
}) {
  const [elapsed, setElapsed] = useState('');

  useEffect(() => {
    if (!startedAt) return;
    const update = () => {
      const diff = Date.now() - new Date(startedAt).getTime();
      const hrs = Math.floor(diff / 3_600_000);
      const mins = Math.floor((diff % 3_600_000) / 60_000);
      const secs = Math.floor((diff % 60_000) / 1_000);
      setElapsed(
        hrs > 0
          ? `${hrs}h ${String(mins).padStart(2, '0')}m ${String(secs).padStart(2, '0')}s`
          : `${mins}m ${String(secs).padStart(2, '0')}s`
      );
    };
    update();
    const iv = setInterval(update, 1_000);
    return () => clearInterval(iv);
  }, [startedAt]);

  return (
    <div className="rounded-xl border border-amber-700/60 bg-amber-950/60 px-5 py-3 flex flex-wrap items-center gap-4">
      <div className="flex items-center gap-2 shrink-0">
        <svg className="w-5 h-5 text-amber-400" fill="currentColor" viewBox="0 0 20 20">
          <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" />
        </svg>
        <span className="text-sm font-semibold text-amber-300 uppercase tracking-wider">Transit Mode</span>
      </div>
      <span className="text-sm text-amber-200/70">
        Mac offline — VPS continuing {taskCount} task{taskCount !== 1 ? 's' : ''}
      </span>
      <span className="ml-auto text-sm font-mono text-amber-300/80">{elapsed}</span>
      <button
        onClick={onReturn}
        disabled={returning}
        className="ml-2 px-4 py-1.5 text-sm rounded-lg bg-green-700 text-green-100 hover:bg-green-600 transition-colors disabled:opacity-50 shrink-0"
      >
        {returning ? 'Returning...' : "I'm Back"}
      </button>
    </div>
  );
}

// /api/system returns agents_hosted as an ARRAY OF NAMES (154 on the VPS) while
// agents_alive is a number. `{arr}` in JSX renders every element concatenated
// with no separator, which is what produced the unreadable name-wall on the VPS
// card — not a missing chip style, a type confusion. The Mac card had the same
// bug and only looked fine because its array is empty.
//
// Rendering the COUNT rather than the names also settles the second half of the
// defect (gm, 2026-08-19): the wall listed retired generations while the
// adjacent number said 48 alive, so the label and the list answered different
// questions. Both numbers are now labelled for exactly what they count —
// "registered" is the registry roster, "alive" is live processes. The readable
// per-agent list already exists in the Agent Fleet table below.
function countOf(v: unknown): number {
  return Array.isArray(v) ? v.length : typeof v === 'number' ? v : 0;
}

function MachineStatusSection({ system }: { system: any }) {
  const machines = system?.machines;
  const sync = system?.sync;
  if (!machines) return null;

  const mac = machines.mac;
  const vps = machines.vps;

  const macStatus = mac?.status || 'offline';
  const vpsStatus = vps?.status || 'online';

  const syncAge = sync?.last_sync
    ? Date.now() - new Date(sync.last_sync).getTime()
    : null;
  const syncColor = !syncAge
    ? 'text-neutral-500'
    : syncAge < 5 * 60_000
      ? 'text-green-400'
      : syncAge < 15 * 60_000
        ? 'text-yellow-400'
        : 'text-red-400';

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Mac card */}
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className={`inline-block w-2.5 h-2.5 rounded-full ${STATUS_DOT[macStatus] || 'bg-red-500'}`} />
            <h3 className="text-sm font-semibold text-neutral-100">MacBook</h3>
            <span className="text-xs text-neutral-500 capitalize">{macStatus}</span>
          </div>
          <div className="space-y-1 text-sm text-neutral-400">
            <p>{countOf(mac?.agents_hosted)} registered</p>
            <p>{mac?.agents_alive ?? 0} alive</p>
            <p>Last heartbeat: {relativeTime(mac?.last_heartbeat)}</p>
            {mac?.consecutive_failures > 0 && (
              <p className="text-amber-400">{mac.consecutive_failures} consecutive failure{mac.consecutive_failures !== 1 ? 's' : ''}</p>
            )}
          </div>
        </div>

        {/* VPS card */}
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-green-500" />
            <h3 className="text-sm font-semibold text-neutral-100">VPS {vps?.hostname ? `(${vps.hostname})` : ''}</h3>
            <span className="text-xs text-neutral-500 capitalize">{vpsStatus}</span>
          </div>
          <div className="space-y-1 text-sm text-neutral-400">
            <p>{countOf(vps?.agents_hosted)} registered</p>
            <p>{vps?.agents_alive ?? 0} alive</p>
            <p>Always on</p>
          </div>
        </div>
      </div>

      {/* Sync status */}
      {sync && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-neutral-500">Sync:</span>
          <span className={syncColor}>
            {sync.last_sync ? `Last sync ${relativeTime(sync.last_sync)}` : 'No sync data'}
          </span>
          {sync.stale && <span className="text-amber-400 font-medium">(stale)</span>}
        </div>
      )}
    </div>
  );
}
