import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { TopBar } from '../components/agent/TopBar';
import { Drawer } from '../components/agent/Drawer';
import { BrainModal } from '../components/agent/BrainModal';
import { Feed } from '../components/agent/Feed';
import { Composer } from '../components/agent/Composer';
import { ReportSheet } from '../components/agent/ReportSheet';
import { useAgentSettings } from '../stores/agentSettings';
import { useAgents } from '../hooks/useAgents';

export default function AgentPage() {
  const { id } = useParams<{ id?: string }>();
  const agentId = id || 'gm';
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const settings = useAgentSettings();
  // A seat's own page names the seat (Shaw's QA run: the composer said "Ask Arturo" on demo-planner's page).
  const { data: agents } = useAgents();
  const seatRow = id ? (agents as Array<{ id: string; generation?: unknown }> | undefined)?.find((a) => a.id === id) : undefined;
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

      {/* Main content: Feed takes most space, leaves room for composer */}
      <div className="flex-1 overflow-hidden flex flex-col min-h-0">
        <Feed agentId={agentId} />
      </div>

      {/* Composer fixed at bottom */}
      <Composer agentId={agentId} seatName={id} />
    </div>
  );
}
