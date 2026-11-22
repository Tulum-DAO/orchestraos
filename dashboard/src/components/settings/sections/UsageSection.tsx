/** Settings › (Server) Usage: each provider's plan windows (UsageOverviewPanel: the rail's mark and ring, a bar per
 *  window), admin-only data. Its own category, so it is found where it is looked for, not under Time zone
 *  (lib/settingsRoute.ts). */
import { PageIntro } from '../SettingsKit';
import { UsageOverviewPanel } from './UsageOverview';

export function UsageSection() {
  return (
    <>
      <PageIntro>How much of each plan is used, and when each limit resets.</PageIntro>
      <UsageOverviewPanel />
    </>
  );
}
