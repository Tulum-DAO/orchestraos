import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { TopBar } from '../components/agent/TopBar';
import { Drawer } from '../components/agent/Drawer';
import { BrainModal } from '../components/agent/BrainModal';
import { Feed } from '../components/agent/Feed';
import { Composer } from '../components/agent/Composer';
import { useAgentSettings } from '../stores/agentSettings';

export default function AgentPage() {
  const { id } = useParams<{ id?: string }>();
  const agentId = id || 'gm';
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const settings = useAgentSettings();

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
      />

      {/* Navigation drawer */}
      <Drawer isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />

      {/* Brain modal */}
      <BrainModal isOpen={brainOpen} onClose={() => setBrainOpen(false)} />

      {/* Main content: Feed takes most space, leaves room for composer */}
      <div className="flex-1 overflow-hidden flex flex-col min-h-0">
        <Feed agentId={agentId} />
      </div>

      {/* Composer fixed at bottom */}
      <Composer agentId={agentId} />
    </div>
  );
}
