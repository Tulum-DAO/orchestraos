/**
 * settingsRoute.ts — Settings as a route that opens OVER the current page (spec §2): /settings is the category list
 * (a phone's first screen), /settings/<section> a category. The page behind is kept in the history entry's state
 * (settingsBg) and every section opened adds one entry (settingsDepth), so browser Back steps back through sections
 * and then closes Settings; Esc / X / Done go back past them all at once.
 */
export type SectionId = 'general' | 'display' | 'voice' | 'agents' | 'models' | 'keys' | 'notifications' | 'people' | 'system' | 'server-voice' | 'server';
/** `group`: listed under a small label at the bottom of the list (the install's own settings, admin). */
export interface SettingsSection { id: SectionId; label: string; visible: boolean; group?: 'server' }

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { id: 'general', label: 'General', visible: true },
  { id: 'display', label: 'Display', visible: true },
  // Yours, on this device: the mic and its colour. The install's voice rows are under Server.
  { id: 'voice', label: 'Voice', visible: true },
  { id: 'agents', label: 'Agents', visible: true },
  { id: 'notifications', label: 'Notifications', visible: true },
  // Phases 2–4 (spec §7): listed so their routes and order are fixed, shown once they are built.
  { id: 'models', label: 'Models & providers', visible: false },
  { id: 'keys', label: 'Keys', visible: false },
  { id: 'people', label: 'Devices & people', visible: false },
  { id: 'system', label: 'System', visible: false },
  // The install's own categories, until a Server settings window exists: last, under the install's name. Its voice
  // provider, default voice, new agents take calls and daily limit (Everyone, admin); then its time zone.
  { id: 'server-voice', label: 'Voice & calls', visible: true, group: 'server' },
  { id: 'server', label: 'Time zone', visible: true, group: 'server' },
];

/** The label above the install's own categories: the install's name, or "Server" while there is none to show. */
export function serverGroupLabel(installName?: string | null): string {
  return installName?.trim() || 'Server';
}

export function visibleSections(): SettingsSection[] { return SETTINGS_SECTIONS.filter((s) => s.visible); }
export function sectionLabel(id: SectionId): string { return SETTINGS_SECTIONS.find((s) => s.id === id)?.label ?? 'Settings'; }
export function settingsPath(section: SectionId | null): string { return section ? `/settings/${section}` : '/settings'; }

/** undefined: not Settings; null: the category list; else the section (an unknown or hidden one opens General). */
export function settingsSectionOf(pathname: string): SectionId | null | undefined {
  const m = /^\/settings(?:\/([^/]*))?\/?$/.exec(pathname);
  if (!m) return undefined;
  if (!m[1]) return null;
  const s = SETTINGS_SECTIONS.find((x) => x.id === m[1] && x.visible);
  return s ? s.id : 'general';
}

export interface BgLocation { pathname: string; search: string; hash: string }
export interface SettingsNavState { settingsBg?: BgLocation; settingsDepth?: number }

export function settingsStateOf(state: unknown): SettingsNavState {
  const s = state as { settingsBg?: unknown; settingsDepth?: unknown } | null;
  const out: SettingsNavState = {};
  const bg = s?.settingsBg as Partial<BgLocation> | undefined;
  if (bg && typeof bg === 'object' && typeof bg.pathname === 'string') {
    out.settingsBg = { pathname: bg.pathname, search: typeof bg.search === 'string' ? bg.search : '', hash: typeof bg.hash === 'string' ? bg.hash : '' };
  }
  if (typeof s?.settingsDepth === 'number' && Number.isInteger(s.settingsDepth) && s.settingsDepth > 0) out.settingsDepth = s.settingsDepth;
  return out;
}

export function openSettingsState(from: BgLocation): SettingsNavState {
  return { settingsBg: { pathname: from.pathname, search: from.search, hash: from.hash }, settingsDepth: 1 };
}

export function nextSettingsState(s: SettingsNavState): SettingsNavState {
  return { ...(s.settingsBg ? { settingsBg: s.settingsBg } : {}), settingsDepth: (s.settingsDepth ?? 0) + 1 };
}

export function backgroundPath(s: SettingsNavState): string {
  return s.settingsBg ? `${s.settingsBg.pathname}${s.settingsBg.search}${s.settingsBg.hash}` : '/';
}

/** How to close: back past every Settings entry; or, opened from a link (nothing of ours behind it), back to the link's
 *  own entry (`back`, when sections were opened since) and then replace it with the page behind (`then` / `to`), so
 *  no /settings entry is left for Back to reopen. */
export function closeSettingsTarget(s: SettingsNavState): { back: number } | { back: number; then: string } | { to: string } {
  // Only entries pushed from a page (settingsBg set) can be gone back past; a deep link has nothing behind it.
  if (s.settingsDepth && s.settingsBg) return { back: s.settingsDepth };
  return s.settingsDepth ? { back: s.settingsDepth, then: backgroundPath(s) } : { to: backgroundPath(s) };
}

export interface KeyLike { key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean }
export function isSettingsShortcut(e: KeyLike, mac: boolean): boolean {
  if (e.key !== ',' || e.altKey) return false;
  return mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
}
export function isMacPlatform(platform: string): boolean { return /Mac|iPhone|iPad|iPod/.test(platform); }
export function settingsShortcutLabel(mac: boolean): string { return mac ? '⌘,' : 'Ctrl+,'; }

export type SettingScopeTag = 'device' | 'user' | 'everyone';
export const SCOPE_LABEL: Record<SettingScopeTag, string> = { device: 'This device', user: 'You', everyone: 'Everyone' };

/** Search: every word of the query appears in the row's label or keywords. */
export function matchesQuery(q: string, label: string, keywords: readonly string[]): boolean {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [label, ...keywords].join(' ').toLowerCase();
  return words.every((w) => hay.includes(w));
}
