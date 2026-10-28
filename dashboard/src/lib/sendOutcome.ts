/**
 * sendOutcome.ts — what the composer is allowed to SAY after a send.
 *
 * A port of the iOS app's SendOutcome, rule for rule, so the web and the phone never disagree
 * about whether a message went. Pure: no React, no network. Pinned by sendOutcome.test.ts, which
 * carries every one of the iOS cases.
 *
 * The case it exists for: the gateway types the keys, presses Enter, then verifies by reading the
 * SCREEN, never the transcript. It false-negatives when the composer cannot be located, or when an
 * idle agent finishes its turn inside the verify window. So a 502 `unverified_submit` means
 * delivery UNKNOWN: go and check the transcript. It never means "not sent", because saying so
 * keeps the draft and trains the person to send it a second time.
 *
 * `send_failed` is the opposite: tmux refused the keystrokes, nothing was typed, and "not sent" is
 * the honest sentence.
 */

export type SendOutcome = 'delivered' | 'notSent' | 'unknown';

/** Reasons that mean the keystrokes never landed, so no transcript probe is needed. */
export const DEFINITE_FAILURE_REASONS: ReadonlySet<string> = new Set(['send_failed']);

/** The keys were sent but could not be VERIFIED from the screen. Unknown, never failed. */
export const UNVERIFIED_REASON = 'unverified_submit';

/**
 * Decide what to say, given the server's reason and the transcript probe.
 * `probe`: true = the transcript shows it, false = checked and absent, null = could not check.
 * A null must never be read as absent: that is how a reachability failure turns into a false
 * claim that the message was lost.
 */
export function classify(reason: string | null | undefined, probe: boolean | null | undefined): SendOutcome {
  if (reason && DEFINITE_FAILURE_REASONS.has(reason)) return 'notSent';
  if (probe === true) return 'delivered';
  if (probe === false) return 'notSent';
  return 'unknown';
}

/** True when this reason must be PROBED rather than reported. Separate from classify so a caller
 *  cannot skip the probe by accident: anything that is not a definite failure has to go and look. */
export function requiresProbe(reason: string | null | undefined): boolean {
  if (!reason) return true;
  return !DEFINITE_FAILURE_REASONS.has(reason);
}

/** Whitespace-collapsing normalizer, shared with the pending-bubble matcher. */
export function normalize(s: string): string {
  return s.split(/\s+/).filter(Boolean).join(' ');
}

/** The first `n` characters (code points, not UTF-16 halves). */
export function prefixChars(s: string, n: number): string {
  return Array.from(s).slice(0, n).join('');
}

/** The tag the delivery layer puts in front of a pointer-injected (long) message. */
export const CHIP_DODGE_TAG = '[LONG-MSG chip-dodge]';

/**
 * A chip-dodged user turn reads
 * `[LONG-MSG chip-dodge] <first part of the text>... — FULL TEXT (N chars): read /tmp/agent-inject-….md`,
 * so the only part of the person's words in it is a TRUNCATED PREFIX. Returns that prefix, or null
 * when the row is not a chip-dodge banner.
 */
export function chipDodgeVisibleText(row: string): string | null {
  const n = normalize(row);
  if (!n.startsWith(CHIP_DODGE_TAG)) return null;
  let body = n.slice(CHIP_DODGE_TAG.length);
  // Cut the pointer tail, whose dash is an em dash in the live format.
  for (const sep of [' — FULL TEXT (', ' - FULL TEXT (', 'FULL TEXT (']) {
    const i = body.indexOf(sep);
    if (i >= 0) { body = body.slice(0, i); break; }
  }
  // The banner elides with "..."; drop it so the remainder is a clean prefix.
  while (body.endsWith('.') || body.endsWith('…') || body.endsWith(' ')) body = body.slice(0, -1);
  const out = body.trim();
  return out ? out : null;
}

/** Minimum overlap before a chip-dodge banner counts as evidence. Short prefixes collide ("ok",
 *  "thanks"), and a false POSITIVE here clears a draft that was never delivered. */
export const MINIMUM_BANNER_OVERLAP = 20;

/** The 60-character match key both reconcilers use. */
export const KEY_LENGTH = 60;

/**
 * Does a transcript user turn evidence the text we sent? (The "did it land?" probe after an
 * unknown outcome.) `pointer` is the server's chip_dodge_pointer: an EXACT token present in the
 * row, checked first. Otherwise the ordinary case is the row containing our key, and the
 * chip-dodge case is our text STARTING WITH the banner's visible prefix (the direction is
 * reversed because the banner is the truncated one).
 */
export function rowEvidences(sent: string, row: string, pointer?: string | null): boolean {
  const nRow = normalize(row);
  if (pointer && nRow.includes(normalize(pointer))) return true;
  const nSent = normalize(sent);
  if (!nSent) return false;
  const key = prefixChars(nSent, KEY_LENGTH);
  if (nRow.includes(key)) return true;
  const visible = chipDodgeVisibleText(row);
  if (visible && Array.from(visible).length >= MINIMUM_BANNER_OVERLAP && nSent.startsWith(visible)) return true;
  return false;
}
