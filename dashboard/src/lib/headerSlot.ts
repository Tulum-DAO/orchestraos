import { createContext } from 'react';

/**
 * A page header that wants the shared notification bell at the end of its row renders an empty
 * slot element and registers it here (a ref callback). DashboardLayout provides the callback and
 * portals its single bell into the registered element (lib/bellPlacement). Null outside the layout
 * (the QA harness), where a header simply has no slot to fill.
 */
export const HeaderEndSlotContext = createContext<((el: HTMLElement | null) => void) | null>(null);
