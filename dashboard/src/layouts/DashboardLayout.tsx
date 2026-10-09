import { useState, useEffect } from 'react';
import { CommandPalette } from '../components/CommandPalette';
import { Outlet } from 'react-router-dom';
import { RouteErrorBoundary } from '../components/RouteErrorBoundary';
import { Menu, X } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { useMatch } from 'react-router-dom';
import { VoiceCallBubble } from '../components/VoiceCallModal';
import NotificationBell from '../components/NotificationBell';
import { ArturoPill } from '../components/arturo/ArturoPill';
import CoachingToast from '../components/CoachingToast';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { initAutoDiscovery } from '../lib/telemetry';
import { showArturoPill } from '../lib/features';

export function DashboardLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // A CHAT ROUTE OWNS ITS OWN VERTICAL SPACE. Shaw: the composer bar spanned the full window and
  // cut the sidebar off at the bottom, because it was laid out at the PAGE level rather than
  // inside the chat column. The root cause is here: <main> scrolled and padded every route the
  // same way, so a page with its own header/transcript/composer column had no height to fill and
  // escaped with `fixed`. Chat routes now get a FLEX COLUMN with no padding and no page scroll —
  // the transcript scrolls inside it, and the composer is a child of the column, not of the page.
  // Both hooks run on EVERY render. `useMatch(a) || useMatch(b)` skipped the second hook on an
  // agent page, so the hook count changed when leaving it: React threw ("change in the order of
  // Hooks"), unmounted the whole tree, and every page after it was black until a reload.
  const onAgentWithId = useMatch('/agent/:id');
  const onAgentBare = useMatch('/agent');
  const isChatRoute = !!onAgentWithId || !!onAgentBare;
  const activeCall = useOrchestraStore((s) => s.activeCall);
  const endCall = useOrchestraStore((s) => s.endCall);

  useEffect(() => {
    initAutoDiscovery();
  }, []);

  return (
    <div className="flex h-screen bg-neutral-950 text-neutral-100">
      {/* Desktop sidebar */}
      <div className="hidden md:block">
        <Sidebar />
      </div>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Mobile sidebar drawer */}
      <div
        className={`fixed inset-y-0 left-0 z-40 transform transition-transform duration-200 ease-in-out md:hidden ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <Sidebar onNavigate={() => setSidebarOpen(false)} />
      </div>

      {/* Main content */}
      <main className={`flex-1 min-w-0 flex flex-col safe-top safe-bottom ${isChatRoute ? 'overflow-hidden' : 'overflow-y-auto'}`}>
        {/* Mobile header with hamburger */}
        <div className="sticky top-0 z-20 flex items-center gap-3 border-b border-neutral-800 bg-neutral-950/90 backdrop-blur px-4 py-3 md:hidden">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-1 -ml-1 text-neutral-400 hover:text-white"
            aria-label="Toggle sidebar"
          >
            {sidebarOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
          <span className="text-sm font-bold tracking-tight text-white">orchestraOS</span>
          <div className="ml-auto">
            <NotificationBell />
          </div>
        </div>
        <CoachingToast />
        {isChatRoute ? (
          <div className="flex-1 min-h-0 flex flex-col">
            <RouteErrorBoundary label="page"><Outlet /></RouteErrorBoundary>
          </div>
        ) : (
          <div className="p-4 md:p-6">
            <RouteErrorBoundary label="page"><Outlet /></RouteErrorBoundary>
          </div>
        )}
      </main>

      {/* Desktop notification bell — fixed top-right */}
      <div className="hidden md:block fixed top-4 right-4 z-30">
        <NotificationBell />
      </div>

      {/* Global voice call bubble — persists across page navigation */}
      {activeCall && (
        <VoiceCallBubble
          pmId={activeCall.pmId}
          agentId={activeCall.agentId}
          onClose={endCall}
        />
      )}

      {/* Cmd-K. Mounted at the layout so it is available on every page, and it renders
          nothing until it is opened. */}
      <CommandPalette />

            {/* Arturo pill — always available on every non-home page (T4); replaces the legacy JarvisPanel */}
      {showArturoPill() && <ArturoPill />}
    </div>
  );
}
