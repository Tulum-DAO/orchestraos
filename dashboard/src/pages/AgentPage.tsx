import { useState, useEffect } from 'react';
import { agentRowsFrom } from '../lib/api';
import { useParams } from 'react-router-dom';
import { TopBar } from '../components/agent/TopBar';
import { Drawer } from '../components/agent/Drawer';
import { BrainModal } from '../components/agent/BrainModal';
import { Feed } from '../components/agent/Feed';
import { Composer } from '../components/agent/Composer';
import { LiveStatusLine } from '../components/chat/LiveStatusLine';
import { useFeedHealth } from '../hooks/useFeedHealth';
import { ReportSheet } from '../components/agent/ReportSheet';
import { useAgentSettings } from '../stores/agentSettings';
import { spawnAgent } from '../lib/api';
import { composerGate } from '../lib/composerGate';
import { useAgents } from '../hooks/useAgents';

export default function AgentPage() {
  const { id } = useParams<{ id?: string }>();
  const agentId = id || 'gm';
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [resuming, setResuming] = useState(false);
  // Restart a seat that fell over. `spawnAgent` is the existing restart path, and this is the
  // first caller of the status line's Resume affordance. Undefined while in flight, so the
  // button disappears rather than queueing a second spawn on a double click.
  const resume = resuming ? undefined : () => {
    setResuming(true);
    void spawnAgent(agentId).finally(() => setResuming(false));
  };
  const settings = useAgentSettings();
  // A seat's own page names the seat (Shaw's QA run: the composer said "Ask Arturo" on demo-planner's page).
  const { data } = useAgents();
  // /api/agents answers { agents: [...] }; tolerate a bare array too.
  const rows = agentRowsFrom(data) as Array<{ id: string; generation?: unknown }> | undefined;
  const seatRow = id && Array.isArray(rows) ? rows.find((a) => a.id === id) : undefined;
  const feed = useFeedHealth();
  const seat = id ? { id, generation: seatRow?.generation } : undefined;

  // Load settings from storage on mount
  useEffect(() => {
    settings.loadFromStorage();
  }, []);

  return (
    <div className="flex flex-col h-full min-h-0 bg-background text-foreground">
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

      {/* Main content. The agent rail lives in the Sidebar now — ONE nav column, not two. */}
      <div className="flex-1 overflow-hidden flex flex-col min-h-0">
        <Feed agentId={agentId} />
      </div>

      {/* THE LIVE STATUS LINE, pinned directly above the composer (Shaw's top charge: nothing on
          the page said what the agent was doing right now, so the sidebar and the page could
          contradict each other). Same row, same staleness verdict as the rail dot. */}
      {/* The Resume branch in LiveStatusLine had NO caller anywhere, so a stopped agent was
          told it would not see the message and offered nothing to do about it. */}
      <LiveStatusLine
        onResume={resume}
        state={(seatRow as { status?: string } | undefined)?.status}
        feed={feed}
        stateAgeS={(seatRow as { state_age_s?: number } | undefined)?.state_age_s}
        tool={(seatRow as { tool?: string } | undefined)?.tool}
      />

      <Composer
        agentId={agentId}
        seatName={id}
        gate={composerGate({
          state: (seatRow as { status?: string } | undefined)?.status,
          pendingMenu: (seatRow as { pending_menu?: unknown } | undefined)?.pending_menu,
          subagents: (seatRow as { subagents?: number } | undefined)?.subagents,
        })}
        subagents={(seatRow as { subagents?: number } | undefined)?.subagents}
      />


    </div>
  );
}
