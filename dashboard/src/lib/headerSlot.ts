import { createContext } from 'react';

/**
 * A page header that wants the shared notification bell at the end of its row renders an empty
 * slot element and registers it here (a ref callback). DashboardLayout provides the callback and
 * portals its single bell into the registered element (lib/bellPlacement). Null outside the layout
 * (the QA harness), where a header simply has no slot to fill.
 */
export const HeaderEndSlotContext = createContext<((el: HTMLElement | null) => void) | null>(null);

/** An agent page's menu button asks DashboardLayout to open its (one) sidebar. */
export const OPEN_SIDEBAR_EVENT = 'orchestra:open-sidebar';
