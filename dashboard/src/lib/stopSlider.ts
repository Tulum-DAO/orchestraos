/**
 * stopSlider.ts — when the stop slider (components/kit/StopSlider.tsx) sends a pick. Its thumb follows the pointer or
 * the arrow keys at once (a local draft), but the pick goes out ONCE, on release: a drag across three stops, or an
 * arrow key held down, is one switch, not one per stop (a switch is a request to the agent). The thumb stays where it
 * was let go until the value it was given changes (the switch landed, or someone else changed it): no snap back.
 *
 * Unset (no level kept: the CLI's default), the hollow thumb rests on a stop the user may want: a pointer release or
 * Enter / Space there picks it, once. A key coming up or a blur with nothing moved picks nothing (tabbing through).
 */

export interface SliderDraft {
  /** The stop the thumb is drawn on. */
  shown: number;
  /** Moved since the last commit. */
  dirty: boolean;
  /** The stop last sent by a tap on the hollow thumb, until the value given catches up (no second send for the
   *  same tap); a move there again always sends (a retry after a refusal). */
  sent: number | null;
}

export type CommitHow = 'pointer' | 'key' | 'blur' | 'activate';

export const sliderDraft = (index: number): SliderDraft => ({ shown: index, dirty: false, sent: null });

export const sliderMove = (d: SliderDraft, i: number): SliderDraft => ({ ...d, shown: i, dirty: true });

/** A release (pointer up, key up, blur) or Enter / Space: the stop to send, if any, and the draft after it. */
export function sliderCommit(d: SliderDraft, prop: number, unset: boolean, how: CommitHow): { draft: SliderDraft; call: number | null } {
  const moved = d.dirty && d.shown !== prop;
  const pickHere = unset && (how === 'pointer' || how === 'activate');
  const call = moved || (pickHere && d.sent !== d.shown) ? d.shown : null;
  return { draft: { shown: d.shown, dirty: false, sent: call ?? d.sent }, call };
}

/** The value given changed (`prev` → `next`): draw it. Unchanged: keep the draft (a drag in progress, a pick on its
 *  way). */
export function sliderSync(d: SliderDraft, prev: number, next: number): SliderDraft {
  return prev === next ? d : sliderDraft(next);
}
