import { clsx } from 'clsx';
import { Search, Plus } from 'lucide-react';

export interface AgentsSummaryStripProps {
  machineName: string;
  machineLive: boolean;
  agentsUp: number;
  agentsTotal: number;
  messages24h: number;
  activeConnections: number;
  busiest: { a: string; b: string; count: number } | null;
  windowHours: number;
  search: string;
  onSearchChange: (next: string) => void;
  /** Open the busiest connection's conversation. review flagged that the strip advertised a
   *  pair the operator could not click or find — often gm<->telegram, which is real traffic
   *  but not a node in the graph. Rather than hide the busiest pair (it IS the busiest), give
   *  it the affordance it was missing. */
  onSelectBusiest?: () => void;
  /** New agent. A plain "+" right after the title (Shaw, 2026-10-10: "the new agent button should
   *  be a plus next to the agents title. We're pursuing a minimalist path"). Absent = no "+": the
   *  caller passes it only when the deployment offers New Agent (showNewAgent). */
  onNewAgent?: () => void;
}

function formatAgentsUp(agentsUp: number, agentsTotal: number): string {
  return `${agentsUp} of ${agentsTotal} up`;
}

function formatBusiest(busiest: { a: string; b: string; count: number } | null): string {
  return busiest ? `${busiest.a} to ${busiest.b}, ${busiest.count}` : '—';
}

export function AgentsSummaryStrip({
  machineName,
  machineLive,
  agentsUp,
  agentsTotal,
  messages24h,
  activeConnections,
  busiest,
  windowHours,
  search,
  onSearchChange,
  onSelectBusiest,
  onNewAgent,
}: AgentsSummaryStripProps) {
  return (
    // review's browser pass (2026-09-30) found two real layout bugs here, both desktop-first:
    //  1. At 1280px the search input overlapped the sibling "New agent" button by 34px, and
    //     hit-testing the input's own right edge returned the BUTTON — the last 34px of the
    //     field was dead to clicks. Cause was `-mx-6 px-6` on the strip: a full-bleed trick
    //     that is correct for a lone scroll row and wrong next to a sibling, because the
    //     negative margin pushes the row out under it. Removed.
    //  2. At 768px "20 active co..." cut mid-word and the busiest number and the search box
    //     vanished entirely; at 375px only two of §3's four numbers survived. Cause was
    //     horizontal SCROLL as the overflow strategy — off-screen reads as absent. Now the
    //     numbers WRAP, so a narrow viewport gets more rows rather than fewer facts. §2's
    //     "nothing important is hidden" applies to viewport width too.
    <div className="space-y-2 min-w-0">
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        <h1 className="text-2xl font-bold text-neutral-100">Agents</h1>
        {onNewAgent && (
          <button
            type="button"
            onClick={onNewAgent}
            aria-label="New agent"
            title="New agent"
            className="touch-circle shrink-0 rounded-full flex items-center justify-center text-neutral-400 hover:text-white hover:bg-neutral-800 transition-colors"
          >
            <Plus size={22} />
          </button>
        )}
        <span
          className={clsx('inline-block w-2 h-2 rounded-full shrink-0', machineLive ? 'bg-green-500 motion-safe:animate-pulse' : 'bg-red-500')}
          title={machineLive ? 'live' : 'down'}
        />
        {/* break-all, not wrap: a hostname has no spaces to break at, so at 375px it used to
            push three lines wide into the old New agent button instead of breaking. */}
        <span className="text-sm text-neutral-500 break-all min-w-0">{machineName}</span>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 min-w-0">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-neutral-400 min-w-0">
          <span className="whitespace-nowrap">{formatAgentsUp(agentsUp, agentsTotal)}</span>
          <span className="whitespace-nowrap">{messages24h} messages ({windowHours}h)</span>
          <span className="whitespace-nowrap">{activeConnections} active connections</span>
          {busiest && onSelectBusiest ? (
            <button
              type="button"
              onClick={onSelectBusiest}
              title={`Open ${busiest.a} ⇄ ${busiest.b}`}
              className="whitespace-nowrap underline decoration-neutral-700 hover:text-neutral-100 focus:outline-none focus:ring-1 focus:ring-sky-500 rounded"
            >
              busiest: {formatBusiest(busiest)}
            </button>
          ) : (
            <span className="whitespace-nowrap">busiest: {formatBusiest(busiest)}</span>
          )}
        </div>
        {/* ALWAYS its own row, left-aligned under the numbers — never `ml-auto`.
            Right-aligning it put it in the sibling "New agent" button's column while it still
            belonged to this one structurally, so the two formed a staircase: the button pinned
            to the title at y=52 with dead space beneath it, the search 40px lower and its right
            edge 149px short of the button's. Measured at 1920/1440/1280 it read as the search
            having shoved the button out of place, and at 1024/768 the gap grew to 66 and 90px.
            They never actually overlapped — this is alignment, not collision (the 1280px
            overlap noted above was a different bug, already fixed).
            `basis-full` forces the new flex line deterministically at every width, rather than
            letting it land beside the numbers whenever they happen to leave room; the search
            results render in flow directly beneath it, so the two stay adjacent. */}
        {/* Two elements, not one: `basis-full` on a flex item wins the sizing over `w-80`
            (flex-basis beats width on the main axis), so a single div stretched to the full
            1435px at 1920. The outer div owns the line break, the inner one the width. */}
        <div className="basis-full w-full">
        <div className="relative w-full sm:w-80">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search agents, repos, prompts"
            className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-100 placeholder:text-neutral-500 focus:outline-none focus:border-neutral-600"
          />
        </div>
        </div>
      </div>
    </div>
  );
}
