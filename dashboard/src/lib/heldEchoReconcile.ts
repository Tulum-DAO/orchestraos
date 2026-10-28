/**
 * heldEchoReconcile.ts — when a just-sent bubble is DONE: its own transcript row has arrived.
 *
 * A port of the iOS app's HeldEchoReconcile, rule for rule, pinned by heldEchoReconcile.test.ts
 * (every iOS case, plus the pointer cases below). The three properties it holds:
 *   (a) RECENCY: only a row that did NOT exist when the bubble was created may confirm it. Carried
 *       as the set of row ids on screen at send time, so there is no clock and no skew window.
 *       An old "yes" must never confirm a "yes" sent seconds ago.
 *   (b) ONE-TO-ONE: a row confirms at most ONE bubble, oldest bubble first. Two identical sends
 *       and one arriving row leave one bubble showing.
 *   (c) ANCHORED: the row must EQUAL the bubble's key or START with it, never merely contain it.
 *       A bare "contains" cleared a pending "ok" against an unrelated "look into it".
 * Clearing too early hides a message that is still pending; clearing too late shows it twice for
 * a moment. Every rule here errs late, and the agent-idle backstop catches what stays.
 *
 * On the web these rules govern EVERY pending bubble, the ordinary just-sent kind as well as the
 * held one. That is deliberately stricter than the iOS app, whose ordinary bubbles still clear on
 * a plain "contains": one rule means one way for a bubble to clear, and (a)-(c) are what stop an
 * older or unrelated row from clearing it early.
 *
 * One addition over iOS: a bubble may carry the server's chip_dodge_pointer. A long message
 * arrives as a short banner naming that /tmp file, which fails (c) by design; the pointer is an
 * exact token, so a NEW row containing it confirms the bubble at once instead of at idle.
 */
import { normalize, prefixChars, KEY_LENGTH } from './sendOutcome.ts';

export interface Echo {
  text: string;
  /** Ids of the user rows already on screen when this bubble was created: property (a). */
  priorRowIds?: ReadonlySet<string>;
  /** The server's chip_dodge_pointer for this send, when it had one. */
  pointer?: string | null;
}

export interface Row { id: string; text: string }

export function matchKey(sent: string): string {
  return prefixChars(normalize(sent), KEY_LENGTH);
}

/** Property (c): EQUAL or STARTS WITH, never contains. */
export function confirms(row: string, key: string): boolean {
  if (!key) return false;
  const n = normalize(row);
  return n === key || n.startsWith(key);
}

function pointerHit(row: string, pointer?: string | null): boolean {
  return !!pointer && normalize(row).includes(normalize(pointer));
}

/**
 * The bubbles still UNCONFIRMED, which must keep rendering. `held` is oldest first.
 * Empty `rows` clears nothing: a failed or empty poll is not evidence of arrival.
 */
export function unconfirmed<E extends Echo>(held: readonly E[], rows: readonly Row[]): E[] {
  if (!held.length || !rows.length) return [...held];
  const consumed = new Set<string>();          // (b) a row is spent once
  const survivors: E[] = [];
  for (const echo of held) {                   // (b) oldest first
    const key = matchKey(echo.text);
    const eligible = (row: Row) => !consumed.has(row.id) && !echo.priorRowIds?.has(row.id);   // (b), (a)
    // The pointer is checked first: it is the only exact token a chip-dodged row carries.
    const hit = rows.find((row) => eligible(row) && pointerHit(row.text, echo.pointer))
      ?? rows.find((row) => eligible(row) && confirms(row.text, key));                         // (c)
    if (hit) consumed.add(hit.id); else survivors.push(echo);
  }
  return survivors;
}
