import { useState, useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { Menu, X } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { VoiceCallBubble } from '../components/VoiceCallModal';
import NotificationBell from '../components/NotificationBell';
import { ArturoPill } from '../components/arturo/ArturoPill';
import CoachingToast from '../components/CoachingToast';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { initAutoDiscovery } from '../lib/telemetry';

export function DashboardLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
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
      <main className="flex-1 overflow-y-auto safe-top safe-bottom">
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
        <div className="p-4 md:p-6">
          <Outlet />
        </div>
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

      {/* Arturo pill — always available on every non-home page (T4); replaces the legacy JarvisPanel */}
      <ArturoPill />
    </div>
  );
}
