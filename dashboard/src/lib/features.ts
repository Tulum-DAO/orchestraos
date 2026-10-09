/**
 * What each runtime feature flag (lib/runtimeConfig) decides, as pure functions, so every
 * surface's on/off rule is tested both ways without a DOM. The components only call these.
 */
import { featureEnabled, type Features } from './runtimeConfig';

const live = (): Features => ({
  arturo: featureEnabled('arturo'),
  newAgent: featureEnabled('newAgent'),
  providerSignIn: featureEnabled('providerSignIn'),
});

/** "/" is the Arturo home; without Arturo it is the Overview, never a page that 404s. */
export function indexPage(f: Features = live()): 'arturo' | 'overview' {
  return f.arturo ? 'arturo' : 'overview';
}

/** Sidebar entries: the Arturo entry (the one pointing at "/") goes when Arturo is off. */
export function navItemsFor<T extends { to: string }>(items: T[], f: Features = live()): T[] {
  return f.arturo ? items : items.filter((i) => i.to !== '/');
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
