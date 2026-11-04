/**
 * displaySettings.ts — Settings › Display, kept per device (localStorage), as plain functions so the rules are tested
 * without a browser. lib/deviceSettings.ts owns the live state; index.html applies the text size before the first
 * paint with the same key and values (keep the two in step).
 *
 *   text size     one factor, --ui-text-scale, scales every text size (index.css @theme --text-*)
 *   spacing       --ui-gap: the space between messages and between sidebar rows
 *   message width --ui-msg-width (the bubble cap, 560 px today), --ui-col-extra (the Modern column)
 *   sidebar width desktop: 240 × text, capped at 300; a drag (200–360) overrides it (orchestra.sidebar.width)
 *   drawer width  phone: ~80% of the screen at 100% text, ~90% at 125–150%
 *   avatars       beside text: 1 + (text − 1) / 2, clamped [0.9, 1.25], never under 16 px; controls never scale
 */
export const TEXT_SCALES = [0.87, 1, 1.12, 1.25, 1.5] as const;
export type TextScale = (typeof TEXT_SCALES)[number];
export const SPACINGS = ['compact', 'comfortable', 'roomy'] as const;
export type Spacing = (typeof SPACINGS)[number];
export const MESSAGE_WIDTHS = ['narrow', 'default', 'wide'] as const;
export type MessageWidth = (typeof MESSAGE_WIDTHS)[number];
export const TOOL_DETAILS = ['summary', 'normal', 'verbose'] as const;
export type ToolDetail = (typeof TOOL_DETAILS)[number];
export const TIMESTAMPS = ['always', 'hover'] as const;
export type Timestamps = (typeof TIMESTAMPS)[number];
export const MOTIONS = ['system', 'reduced', 'full'] as const;
export type Motion = (typeof MOTIONS)[number];

export interface Display {
  textScale: TextScale;
  spacing: Spacing;
  messageWidth: MessageWidth;
  toolDetail: ToolDetail;
  timestamps: Timestamps;
  motion: Motion;
  /** A dragged width (desktop), or null for the text-driven default. */
  sidebarWidth: number | null;
}

export const DISPLAY_KEYS: Record<keyof Display, string> = {
  textScale: 'orchestra.display.textScale',
  spacing: 'orchestra.display.spacing',
  messageWidth: 'orchestra.display.messageWidth',
  toolDetail: 'orchestra.display.toolDetail',
  timestamps: 'orchestra.display.timestamps',
  motion: 'orchestra.display.motion',
  sidebarWidth: 'orchestra.sidebar.width',
};

export const DISPLAY_DEFAULTS: Display = {
  textScale: 1, spacing: 'comfortable', messageWidth: 'default', toolDetail: 'normal', timestamps: 'always', motion: 'system', sidebarWidth: null,
};

export const SIDEBAR_MIN = 200;
export const SIDEBAR_MAX = 360;
const SIDEBAR_BASE = 240;
const SIDEBAR_CAP = 300;
export const GAP: Record<Spacing, number> = { compact: 0.75, comfortable: 1, roomy: 1.25 };
export const MSG_WIDTH: Record<MessageWidth, { bubble: number; column: number }> = {
  narrow: { bubble: 480, column: -120 }, default: { bubble: 560, column: 0 }, wide: { bubble: 720, column: 200 },
};

export function defaultSidebarWidth(t: number): number {
  return Math.min(SIDEBAR_CAP, Math.round((SIDEBAR_BASE * t) / 10) * 10);
}
export function clampSidebarWidth(px: number): number {
  return Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(px)));
}
export function sidebarWidth(d: Display): number {
  return d.sidebarWidth ?? defaultSidebarWidth(d.textScale);
}
export function drawerWidthVw(t: number): number {
  return t >= 1.25 ? 90 : t >= 1.12 ? 85 : 80;
}
export function avatarFactor(t: number): number {
  return Math.min(1.25, Math.max(0.9, Math.round((1 + (t - 1) / 2) * 1000) / 1000));
}
export function textAvatarPx(base: number, t: number): number {
  return Math.max(16, Math.round(base * avatarFactor(t)));
}

type Reader = Pick<Storage, 'getItem'> | null | undefined;
type Writer = Pick<Storage, 'setItem' | 'removeItem'> | null | undefined;
const pick = <T extends string>(vals: readonly T[], raw: string | null, d: T): T => ((vals as readonly string[]).includes(raw ?? '') ? (raw as T) : d);

export function readDisplay(s: Reader): Display {
  const get = (k: keyof Display): string | null => { try { return s?.getItem(DISPLAY_KEYS[k]) ?? null; } catch { return null; } };
  const t = Number(get('textScale'));
  const wRaw = get('sidebarWidth');
  const w = Number(wRaw);
  return {
    textScale: (TEXT_SCALES as readonly number[]).includes(t) ? (t as TextScale) : DISPLAY_DEFAULTS.textScale,
    spacing: pick(SPACINGS, get('spacing'), DISPLAY_DEFAULTS.spacing),
    messageWidth: pick(MESSAGE_WIDTHS, get('messageWidth'), DISPLAY_DEFAULTS.messageWidth),
    toolDetail: pick(TOOL_DETAILS, get('toolDetail'), DISPLAY_DEFAULTS.toolDetail),
    timestamps: pick(TIMESTAMPS, get('timestamps'), DISPLAY_DEFAULTS.timestamps),
    motion: pick(MOTIONS, get('motion'), DISPLAY_DEFAULTS.motion),
    sidebarWidth: wRaw !== null && wRaw.trim() !== '' && Number.isFinite(w) ? clampSidebarWidth(w) : null,
  };
}

/** Store one setting; the default (or null) removes the key, so a later default change reaches this device. */
export function writeDisplay<K extends keyof Display>(s: Writer, key: K, value: Display[K]): void {
  try {
    if (value === null || value === DISPLAY_DEFAULTS[key]) s?.removeItem(DISPLAY_KEYS[key]);
    else s?.setItem(DISPLAY_KEYS[key], String(value));
  } catch { /* private mode: this page still follows */ }
}

export function displayVars(d: Display): Record<string, string> {
  const w = MSG_WIDTH[d.messageWidth];
  return {
    '--ui-text-scale': String(d.textScale),
    '--ui-gap': String(GAP[d.spacing]),
    '--ui-msg-width': `${w.bubble}px`,
    '--ui-col-extra': `${w.column}px`,
    '--sidebar-w': `${sidebarWidth(d)}px`,
    '--drawer-w': `${drawerWidthVw(d.textScale)}vw`,
  };
}

export function displayAttrs(d: Display): Record<string, string> {
  return { 'data-motion': d.motion, 'data-timestamps': d.timestamps };
}

export interface DisplayTarget { style: { setProperty(k: string, v: string): void }; setAttribute(k: string, v: string): void }

export function applyDisplay(d: Display, el: DisplayTarget): void {
  for (const [k, v] of Object.entries(displayVars(d))) el.style.setProperty(k, v);
  for (const [k, v] of Object.entries(displayAttrs(d))) el.setAttribute(k, v);
}
