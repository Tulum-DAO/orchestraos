/**
 * Where the ONE notification bell (#400) renders. It is mounted once, in DashboardLayout, and
 * portalled into the header row it belongs to, so it is laid out BY that row (same flex line,
 * same centre) instead of being a fixed box that guesses the row's height. Pure, so the rule is
 * tested without a DOM; the layout only supplies the slots that are mounted right now.
 *
 * - desktop, a page header with an end slot (the agent page's TopBar): the end of that row;
 * - phone on an agent page: the page header (the layout's phone bar is hidden there, so the page
 *   has ONE bar, not two stacked);
 * - phone elsewhere: the end of the layout's own phone bar;
 * - otherwise (desktop pages without their own header): the fixed top-right corner, as before.
 */
export type BellHost = 'page' | 'phone' | 'corner';

export function bellHost(opts: { desktop: boolean; pageSlot: boolean; phoneSlot: boolean; chatRoute?: boolean }): BellHost {
  if (opts.desktop) return opts.pageSlot ? 'page' : 'corner';
  if (opts.chatRoute && opts.pageSlot) return 'page';
  return opts.phoneSlot ? 'phone' : 'corner';
}
