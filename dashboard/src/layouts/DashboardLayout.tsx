import { useState, useEffect, useRef, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { CommandPalette } from '../components/CommandPalette';
import { Outlet, useLocation } from 'react-router-dom';
import { RouteErrorBoundary } from '../components/RouteErrorBoundary';
import { Menu, X, Plus } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { useMatch } from 'react-router-dom';
import { VoiceCallBubble } from '../components/VoiceCallModal';
import NotificationBell from '../components/NotificationBell';
import { ArturoPill } from '../components/arturo/ArturoPill';
import { FloatingArturo } from '../components/arturo/FloatingArturo';
import CoachingToast from '../components/CoachingToast';
import { useOrchestraStore } from '../stores/useOrchestraStore';
import { initAutoDiscovery } from '../lib/telemetry';
import { showArturoPill, isViewHidden, showNewAgent } from '../lib/features';
import NotFound from '../components/NotFound';
import { HeaderEndSlotContext } from '../lib/headerSlot';
import { bellHost } from '../lib/bellPlacement';
import { OPEN_SIDEBAR_EVENT } from '../lib/headerSlot';
import { sidebarCollapsed, subscribeSidebarCollapsed } from '../lib/sidebarCollapse';
import { NEW_AGENT_EVENT } from '../lib/newAgent';

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
  // An agent page's own menu button opens THIS sidebar (one nav menu, not a second, older one).
  useEffect(() => {
    const open = () => setSidebarOpen(true);
    window.addEventListener(OPEN_SIDEBAR_EVENT, open);
    return () => window.removeEventListener(OPEN_SIDEBAR_EVENT, open);
  }, []);
  // THE VISIBLE AREA, published as --vvh / --vv-top / --kb. On iPhone the keyboard shrinks the
  // visual viewport but not 100dvh, and Safari pans the page to show the focused field. The app
  // root is pinned to the visible area (fixed, top = --vv-top, height = --vvh), so whatever is
  // docked at its bottom (the composer, the Dev key bar and the CLI line above it) sits right on
  // the keyboard. --kb is how much of the layout viewport the keyboard covers, for fixed overlays.
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const root = document.documentElement;
    let last = '';
    // On a phone, outside Dev mode, Safari's scroll to the field is undone (unscroll below), so the view never
    // really moves: its offset is pinned at 0. Following the offset Safari reports for a few frames after the
    // undo pushed the whole page down by the keyboard's height (a black band above the top bar), then slid it
    // back up faster than the keyboard (seen frame by frame on a phone). Dev mode keeps following it.
    const pinned = () => window.matchMedia('(max-width: 768px)').matches
      && !(document.activeElement as HTMLElement | null)?.closest?.('[data-keyboard-native]');
    const apply = () => {
      const offset = pinned() ? 0 : vv.offsetTop;
      const h = Math.round(vv.height), top = Math.round(offset);
      const kb = Math.max(0, Math.round(window.innerHeight - vv.height - offset));
      const key = `${h}|${top}|${kb}`;
      if (key === last) return;
      last = key;
      root.style.setProperty('--vvh', `${h}px`);
      root.style.setProperty('--vv-top', `${top}px`);
      root.style.setProperty('--kb', `${kb}px`);
    };
    // FLY-UP: Safari slides the view the moment a field is tapped, but tells us
    // (resize / scroll) only later, so the pinned root lagged a beat behind and everything jumped up, then
    // back. While a keyboard opens or closes, follow the visible area EVERY FRAME instead of waiting for
    // the events; at rest, the events are enough.
    let frame = 0, until = 0;
    const follow = () => { apply(); frame = performance.now() < until ? requestAnimationFrame(follow) : 0; };
    const track = () => { until = performance.now() + 1000; if (!frame) frame = requestAnimationFrame(follow); apply(); };
    const onFocus = (e: FocusEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.isContentEditable)) track();
    };
    // FLY-UP, measured on a phone with a viewport readout: the keyboard opened
    // (visible area 760 -> 482), then Safari scrolled the WINDOW up 278px to show the field, and only ~500 ms
    // later reported it (visualViewport.offsetTop 278), when the root was moved back down to match. The root is
    // pinned to the visible area and already sits above the keyboard, so the window never needs to scroll: on
    // a phone its scroll is undone at once. Dev mode opts out (data-keyboard-native) and keeps its behaviour.
    const unscroll = () => {
      if (window.scrollY === 0 && window.scrollX === 0) return;
      if (!window.matchMedia('(max-width: 768px)').matches) return;
      if ((document.activeElement as HTMLElement | null)?.closest?.('[data-keyboard-native]')) return;
      window.scrollTo(0, 0);
      apply();
    };
    apply();
    vv.addEventListener('resize', track);
    vv.addEventListener('scroll', track);
    document.addEventListener('focusin', onFocus, true);
    document.addEventListener('focusout', onFocus, true);
    window.addEventListener('scroll', unscroll, { passive: true });
    return () => {
      vv.removeEventListener('resize', track); vv.removeEventListener('scroll', track);
      document.removeEventListener('focusin', onFocus, true); document.removeEventListener('focusout', onFocus, true);
      window.removeEventListener('scroll', unscroll);
      cancelAnimationFrame(frame);
    };
  }, []);
  // THE PHONE TOP BAR'S BOTTOM EDGE, published as --topbar-h: the Arturo pane opens under it, and
  // the Agents-page chat panel starts below it so the bar (Arturo + the bell) stays usable while
  // the panel is open (Shaw, 2026-10-10). 0 on desktop, where this bar is hidden.
  const topbarRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const bar = topbarRef.current;
    if (!bar) return;
    const root = document.documentElement;
    // Relative to the app root (which follows the visible area), so overlays add --vv-top.
    const apply = () => {
      const top = appRef.current ? appRef.current.getBoundingClientRect().top : 0;
      root.style.setProperty('--topbar-h', `${Math.max(0, Math.round(bar.getBoundingClientRect().bottom - top))}px`);
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(bar);
    window.addEventListener('resize', apply);
    window.visualViewport?.addEventListener('resize', apply);
    return () => { ro.disconnect(); window.removeEventListener('resize', apply); window.visualViewport?.removeEventListener('resize', apply); root.style.removeProperty('--topbar-h'); };
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
  // Arturo's home is laid out like a chat: its own top bar, a feed that scrolls itself, the composer at the
  // bottom. It is also where the floating Arturo would open, so that is not shown on top of it.
  const onArturoHome = !!useMatch('/');
  const isChatRoute = !!onAgentWithId || !!onAgentBare || onArturoHome;
  // The home's menu button can collapse the desktop sidebar. Only the home honours it: no other page
  // has a button to bring the sidebar back, so they always show it.
  const collapsed = useSyncExternalStore(subscribeSidebarCollapsed, sidebarCollapsed, () => false);
  const hideDesktopSidebar = onArturoHome && collapsed;
  const onAgents = !!useMatch('/agents');
  const activeCall = useOrchestraStore((s) => s.activeCall);
  const endCall = useOrchestraStore((s) => s.endCall);

  useEffect(() => {
    initAutoDiscovery();
  }, []);

  const bell = <NotificationBell />;

  return (
    <div ref={appRef} className="fixed inset-x-0 flex h-screen h-dvh bg-background text-neutral-900 dark:text-neutral-100" style={{ top: 'var(--vv-top, 0px)', height: 'var(--vvh, 100dvh)' }}>
      {/* h-dvh, not h-screen: on iPhone Safari 100vh includes the strip behind the address bar, so a
          bottom-docked composer sat under it and could not be typed into (Shaw, 2026-10-11). */}
      {/* Desktop sidebar */}
      <div className={hideDesktopSidebar ? 'hidden' : 'hidden md:block'}>
        <Sidebar />
      </div>

      {/* Mobile sidebar overlay. z 65/66: above the floating agent card (z-50, its toasts z-60), below Arturo (76). */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-[65] bg-black/60 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Mobile sidebar drawer */}
      <div
        className={`fixed inset-y-0 left-0 z-[66] transform transition-transform duration-200 ease-in-out md:hidden ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <Sidebar onNavigate={() => setSidebarOpen(false)} />
      </div>

      {/* Main content */}
      <main className={`flex-1 min-w-0 flex flex-col safe-top safe-bottom ${isChatRoute ? 'overflow-hidden' : 'overflow-y-auto'}`}>
        {/* Mobile header with hamburger: it follows the theme (tone colours), so no forced dark scope here */}
        <div ref={topbarRef} className={`sticky top-0 z-[25] items-center gap-3 border-b border-tone-800 bg-tone-950/90 backdrop-blur px-4 py-3 md:hidden ${isChatRoute ? 'hidden' : 'flex'}`}>
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-1 -ml-1 text-tone-400 hover:text-tone-white"
            aria-label="Toggle sidebar"
          >
            {sidebarOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
          {/* The page's title lives HERE on a phone, not in a second row under this bar. */}
          {onAgents ? (
            <div className="flex items-center gap-1 min-w-0">
              <span className="text-lg font-bold tracking-tight text-tone-white">Agents</span>
              {showNewAgent() && (
                <button type="button" onClick={() => window.dispatchEvent(new Event(NEW_AGENT_EVENT))} aria-label="New agent" title="New agent"
                  className="touch-circle shrink-0 rounded-full flex items-center justify-center text-tone-400 hover:text-tone-white hover:bg-tone-800">
                  <Plus size={22} />
                </button>
              )}
            </div>
          ) : (
            <span className="text-sm font-bold tracking-tight text-tone-white">orchestraOS</span>
          )}
          {/* Arturo's one spot, then the shared bell's phone home (portalled in below). */}
          <div className="ml-auto flex items-center gap-1">
            <div ref={setPhoneBellSlot} data-phone-bell-slot className="flex items-center" />
          </div>
        </div>
        <CoachingToast />
        <HeaderEndSlotContext.Provider value={setPageBellSlot}>
        {isChatRoute ? (
          <div className="flex-1 min-h-0 flex flex-col" data-layout-chat>
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
        const host = bellHost({ desktop, pageSlot: !!pageBellSlot, phoneSlot: !!phoneBellSlot, chatRoute: isChatRoute });
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


      {!onArturoHome && showArturoPill() && <FloatingArturo />}

      {/* Arturo's pane (T4; replaces the legacy JarvisPanel). Opens right above the floating button. */}
      {!onArturoHome && showArturoPill() && <ArturoPill />}
    </div>
  );
}
