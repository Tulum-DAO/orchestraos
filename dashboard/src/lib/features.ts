/**
 * What each runtime feature flag (lib/runtimeConfig) decides, as pure functions, so every
 * surface's on/off rule is tested both ways without a DOM. The components only call these.
 */
import { featureEnabled, hiddenViews, type Features } from './runtimeConfig';

const live = (): Features => ({
  arturo: featureEnabled('arturo'),
  newAgent: featureEnabled('newAgent'),
  providerSignIn: featureEnabled('providerSignIn'),
});

/** "/" is the Arturo home; without Arturo it is the Overview, never a page that 404s. */
export function indexPage(f: Features = live()): 'arturo' | 'overview' {
  return f.arturo ? 'arturo' : 'overview';
}

/** Is this location inside a view the deployment hides? "/tasks" hides /tasks and /tasks/…, never
 *  /tasks-archive. Case-insensitive, like the router. */
export function isViewHidden(pathname: string, hidden: string[] = hiddenViews()): boolean {
  const p = pathname.toLowerCase().replace(/\/+$/, '') || '/';
  return hidden.some((h) => p === h || p.startsWith(h + '/'));
}

/** Sidebar / palette entries that may be shown: the Arturo entry (the one pointing at "/") goes
 *  when Arturo is off, and every entry inside a hidden view goes. */
export function navItemsFor<T extends { to: string }>(items: T[], f: Features = live(), hidden: string[] = hiddenViews()): T[] {
  return items.filter((i) => (f.arturo || i.to !== '/') && !isViewHidden(i.to, hidden));
}

export function showArturoPill(f: Features = live()): boolean {
  return f.arturo;
}

export function showNewAgent(f: Features = live()): boolean {
  return f.newAgent;
}

/** What tapping a provider tile does. A usable provider expands to its models (always); an
 *  unusable one opens the connect modal only when provider sign-in is on, else nothing. */
export function providerTileAction(selectable: boolean, f: Features = live()): 'expand' | 'connect' | 'none' {
  if (selectable) return 'expand';
  return f.providerSignIn ? 'connect' : 'none';
}

/** The "Add a provider" tile opens an install terminal (login-shell). */
export function showAddProvider(f: Features = live()): boolean {
  return f.providerSignIn;
}
