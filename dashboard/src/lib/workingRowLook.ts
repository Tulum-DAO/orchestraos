/**
 * workingRowLook.ts — the agent working row's look, in one place (components/agent/AgentWorkingRow.tsx),
 * taken from the reference design.
 *
 * Everything scales from two settings: the avatar size A (px) and the text size (px). Sizes and distances are shares
 * of A. The melt itself (the body becoming the middle dot, the side dots, the brightness wave) is avatarCore's MELT,
 * in the avatar's unit space, shared with the call bar and the Swift port.
 */

/** Today's avatar and text sizes: the fleet-wide settings will replace these defaults. */
export const WORKING_ROW_AVATAR = 24;
export const WORKING_ROW_TEXT = 13;

/** Avatar to text, as a share of A. */
export const ROW_GAP_A = 0.73;
/** The working bob's travel, as a share of A (the sidebar's own bob is smaller). */
export const ROW_BOB_A = 0.14;
/** The reply's rise as it lands, as a share of A. */
export const REPLY_RISE_A = 0.39;

/** The reference body (a rounded square) is A across: the avatar canvas that draws it is larger, so the body fills A
 *  and the dots can overhang it. avatarCore: body half width 0.86 units, 1 unit = LAYOUT.u of the canvas. */
export const BODY_HALF_UNITS = 0.86;
export function canvasSizeFor(a: number, layoutU: number): number {
  return a / (2 * BODY_HALF_UNITS * layoutU);
}
/** The state bob is `bob` units of half travel; the row's should be ROW_BOB_A of A: the multiplier for the canvas. */
export function bobScaleFor(stateBobUnits: number): number {
  // 1 unit = A / (2 * BODY_HALF_UNITS) px, so the row's half travel in units is ROW_BOB_A / 2 * 2 * BODY_HALF_UNITS
  return stateBobUnits > 0 ? (ROW_BOB_A * BODY_HALF_UNITS) / stateBobUnits : 1;
}

/** The phrase's shimmer: a lighter band sweeping left to right, once per SHIMMER_MS. Colours live in agentWorking.css. */
export const SHIMMER_MS = 700;

/** The row's sizes for this viewer: the fleet-wide avatar-size and text-size settings will feed this; until then a
 *  per-browser override (localStorage "orchestra.workingRowSizes" = {"avatar":N,"text":N}) or today's defaults. */
export function workingRowSizes(): { avatar: number; text: number } {
  let o: { avatar?: unknown; text?: unknown } | null = null;
  try { o = JSON.parse(localStorage.getItem('orchestra.workingRowSizes') || 'null'); } catch { o = null; }
  const num = (v: unknown, d: number, lo: number, hi: number) => (typeof v === 'number' && v >= lo && v <= hi ? v : d);
  return { avatar: num(o?.avatar, WORKING_ROW_AVATAR, 12, 64), text: num(o?.text, WORKING_ROW_TEXT, 9, 28) };
}
