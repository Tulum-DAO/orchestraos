import { useSyncExternalStore } from 'react';
import { Menu } from 'lucide-react';
import { OPEN_SIDEBAR_EVENT } from '../lib/headerSlot';
import { sidebarCollapsed, setSidebarCollapsed, subscribeSidebarCollapsed } from '../lib/sidebarCollapse';

// Tailwind's `md` breakpoint, where DashboardLayout shows its sidebar beside the page.
const DESKTOP_QUERY = '(min-width: 48rem)';
const subscribeDesktop = (cb: () => void) => {
  const mq = window.matchMedia(DESKTOP_QUERY);
  mq.addEventListener('change', cb);
  return () => mq.removeEventListener('change', cb);
};
const isDesktopNow = () => window.matchMedia(DESKTOP_QUERY).matches;

/**
 * The page header's sidebar button: one control at every width, in the agent top bar's icon-button
 * colours. On a phone it opens the shared slide-in sidebar, as agent pages do; on a desktop, where the
 * sidebar sits beside the page, it collapses and expands it (lib/sidebarCollapse).
 */
export function SidebarMenuButton() {
  const desktop = useSyncExternalStore(subscribeDesktop, isDesktopNow, () => true);
  const collapsed = useSyncExternalStore(subscribeSidebarCollapsed, sidebarCollapsed, () => false);
  const showing = desktop && !collapsed;
  return (
    <button
      type="button"
      onClick={() => {
        if (desktop) setSidebarCollapsed(!collapsed);
        else window.dispatchEvent(new Event(OPEN_SIDEBAR_EVENT));
      }}
      aria-label={showing ? 'Collapse sidebar' : 'Open sidebar'}
      aria-expanded={showing}
      // One step below the agent bar's 24px icon (a 36px box). hit-44 keeps a 44px tap area on a phone.
      className="hit-44 p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
    >
      <Menu size={20} />
    </button>
  );
}
