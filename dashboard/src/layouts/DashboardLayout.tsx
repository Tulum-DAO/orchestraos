import { useState, useEffect, useRef, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { CommandPalette } from '../components/CommandPalette';
import { Outlet, useLocation } from 'react-router-dom';
import { RouteErrorBoundary } from '../components/RouteErrorBoundary';
import { Menu, X } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { useMatch } from 'react-router-dom';
import { VoiceCallBubble } from '../components/VoiceCallModal';
import NotificationBell from '../components/NotificationBell';
import { ArturoPill } from '../components/arturo/ArturoPill';
import { ArturoButton } from '../components/arturo/ArturoButton';
import CoachingToast from '../components/CoachingToast';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { initAutoDiscovery } from '../lib/telemetry';
import { showArturoPill, isViewHidden } from '../lib/features';
import NotFound from '../components/NotFound';
import { HeaderEndSlotContext } from '../lib/headerSlot';
import { bellHost } from '../lib/bellPlacement';

// Tailwind's `md` breakpoint (48rem), read live so the bell follows the bar that is showing.
const DESKTOP_QUERY = '(min-width: 48rem)';
const subscribeDesktop = (cb: () => void) => {
  const mq = window.matchMedia(DESKTOP_QUERY);
  mq.addEventListener('change', cb);
  return () => mq.removeEventListener('change', cb);
};
const isDesktopNow = () => window.matchMedia(DESKTOP_QUERY).matches;

export function DashboardLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // THE ONE BELL'S HOME (#400 mounts it once). A page header registers an end slot through
  // HeaderEndSlotContext (the agent TopBar does); the phone bar below has its own. The bell is
  // portalled into the slot for the bar that is showing, so the ROW lays it out: same height,
  // same centre line as its neighbours, whatever that row's padding is.
  const [pageBellSlot, setPageBellSlot] = useState<HTMLElement | null>(null);
  const [phoneBellSlot, setPhoneBellSlot] = useState<HTMLElement | null>(null);
  const desktop = useSyncExternalStore(subscribeDesktop, isDesktopNow, () => true);
  // THE PHONE TOP BAR'S BOTTOM EDGE, published as --topbar-h: the Arturo pane opens under it, and
  // the Agents-page chat panel starts below it so the bar (Arturo + the bell) stays usable while
  // the panel is open (Shaw, 2026-10-10). 0 on desktop, where this bar is hidden.
  const topbarRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const bar = topbarRef.current;
    if (!bar) return;
    const root = document.documentElement;
    const apply = () => root.style.setProperty('--topbar-h', `${Math.max(0, Math.round(bar.getBoundingClientRect().bottom))}px`);
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(bar);
    window.addEventListener('resize', apply);
    return () => { ro.disconnect(); window.removeEventListener('resize', apply); root.style.removeProperty('--topbar-h'); };
  }, []);
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
  // A view the deployment hides (runtime config hiddenViews) is Page not found, deep links
  // included; the one gate for every route under this layout.
  const location = useLocation();
  const page = isViewHidden(location.pathname) ? <NotFound /> : <Outlet />;
  const isChatRoute = !!onAgentWithId || !!onAgentBare;
  const activeCall = useOrchestraStore((s) => s.activeCall);
  const endCall = useOrchestraStore((s) => s.endCall);

  useEffect(() => {
    initAutoDiscovery();
  }, []);

  const bell = <NotificationBell />;

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
        <div ref={topbarRef} className="dark sticky top-0 z-20 flex items-center gap-3 border-b border-neutral-800 bg-neutral-950/90 backdrop-blur px-4 py-3 md:hidden">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-1 -ml-1 text-neutral-400 hover:text-white"
            aria-label="Toggle sidebar"
          >
            {sidebarOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
          <span className="text-sm font-bold tracking-tight text-white">orchestraOS</span>
          {/* Arturo's one spot, then the shared bell's phone home (portalled in below). */}
          <div className="ml-auto flex items-center gap-1">
            {showArturoPill() && <ArturoButton />}
            <div ref={setPhoneBellSlot} data-phone-bell-slot className="flex items-center" />
          </div>
        </div>
        <CoachingToast />
        <HeaderEndSlotContext.Provider value={setPageBellSlot}>
        {isChatRoute ? (
          <div className="flex-1 min-h-0 flex flex-col">
            <RouteErrorBoundary label="page">{page}</RouteErrorBoundary>
          </div>
        ) : (
          <div className="p-4 md:p-6">
            <RouteErrorBoundary label="page">{page}</RouteErrorBoundary>
          </div>
        )}
        </HeaderEndSlotContext.Provider>
      </main>

      {/* One bell for every route: one polling loop, one dropdown state. It renders INSIDE the
          header row that is showing (lib/bellPlacement), and only desktop pages without their own
          header keep the fixed top-right corner. */}
      {(() => {
        const host = bellHost({ desktop, pageSlot: !!pageBellSlot, phoneSlot: !!phoneBellSlot });
        if (host === 'page' && pageBellSlot) return createPortal(bell, pageBellSlot);
        if (host === 'phone' && phoneBellSlot) return createPortal(bell, phoneBellSlot);
        return <div className="fixed right-4 top-[calc(env(safe-area-inset-top)+0.75rem)] z-30 md:top-4">{bell}</div>;
      })()}

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

      {/* Arturo's pane (T4; replaces the legacy JarvisPanel). Opened by ArturoButton in the top bar. */}
      {showArturoPill() && <ArturoPill />}
    </div>
  );
}
