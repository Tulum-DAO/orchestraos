import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { TopBar } from '../components/agent/TopBar';
import { Drawer } from '../components/agent/Drawer';
import { BrainModal } from '../components/agent/BrainModal';
import { Feed } from '../components/agent/Feed';
import { Composer } from '../components/agent/Composer';
import { ReportSheet } from '../components/agent/ReportSheet';
import { useAgentSettings } from '../stores/agentSettings';
import { useAgents } from '../hooks/useAgents';
import { AgentRail } from '../components/workbench/AgentRail';

export default function AgentPage() {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const agentId = id || 'gm';
  // The rail is a slide-over under lg (PLAN C7: it closes on selection at 390x844) and a fixed
  // column at lg and up. Open state only matters on the small layout.
  const [railOpen, setRailOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const settings = useAgentSettings();
  // A seat's own page names the seat (Shaw's QA run: the composer said "Ask Arturo" on demo-planner's page).
  const { data } = useAgents();
  // /api/agents answers { agents: [...] }; tolerate a bare array too.
  const rows = (Array.isArray(data) ? data : (data as { agents?: unknown } | undefined)?.agents) as Array<{ id: string; generation?: unknown }> | undefined;
  const seatRow = id && Array.isArray(rows) ? rows.find((a) => a.id === id) : undefined;
  const seat = id ? { id, generation: seatRow?.generation } : undefined;

  // Load settings from storage on mount
  useEffect(() => {
    settings.loadFromStorage();
  }, []);

  return (
    <div className="flex flex-col h-screen bg-background text-foreground">
      {/* Top bar with menu, theme toggle, brain */}
      <TopBar
        onMenuOpen={() => setDrawerOpen(true)}
        onBrainOpen={() => setBrainOpen(true)}
        onReportOpen={() => setReportOpen(true)}
        seat={seat}
      />

      {/* Report sheet — RED ALERT front door (docs/RED_ALERT.md) */}
      <ReportSheet isOpen={reportOpen} onClose={() => setReportOpen(false)} agentId={agentId} />

      {/* Navigation drawer */}
      <Drawer isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />

      {/* Brain modal */}
      <BrainModal isOpen={brainOpen} onClose={() => setBrainOpen(false)} />

      {/* Main content: the agent rail beside the chat. Before this you could not switch agent
          from inside a chat at all — the gap PLAN C7 names. */}
      <div className="flex-1 overflow-hidden flex min-h-0">
        {/* lg and up: a real column. The chat keeps its own scroll. */}
        <aside className="hidden lg:flex lg:w-64 xl:w-72 shrink-0 border-r border-neutral-800 min-h-0">
          <AgentRail currentId={agentId} onPick={(next) => navigate(`/agent/${next}`)} />
        </aside>

        {/* Under lg: a slide-over that CLOSES ON SELECTION, so a tap does not leave it covering
            the chat it just navigated to. */}
        {railOpen && (
          <div className="lg:hidden fixed inset-0 z-40 flex">
            <div
              className="absolute inset-0 bg-black/60"
              onClick={() => setRailOpen(false)}
              aria-hidden="true"
            />
            <aside className="relative z-10 w-72 max-w-[85vw] bg-neutral-900 border-r border-neutral-800 flex min-h-0">
              <AgentRail
                currentId={agentId}
                onPick={(next) => { setRailOpen(false); navigate(`/agent/${next}`); }}
              />
            </aside>
          </div>
        )}

        <div className="flex-1 overflow-hidden flex flex-col min-h-0">
          <Feed agentId={agentId} />
        </div>
      </div>

      {/* Composer fixed at bottom */}
      <Composer agentId={agentId} seatName={id} />

      {/* Rail handle, small screens only. Sits above the composer, never over it. */}
      <button
        type="button"
        onClick={() => setRailOpen(true)}
        className="lg:hidden fixed left-2 bottom-20 z-30 px-3 py-2 rounded-full bg-neutral-800/90 border border-neutral-700 text-xs text-neutral-200 shadow"
        aria-label="Show agents"
      >
        Agents
      </button>
    </div>
  );
}
