/**
 * reveal.ts — the word-by-word reveal (SMOOTH_STREAM_SPEC.md), shared by every place that shows words arriving: the
 * live call's transcript now, the streamed reply later. Pure: the queue and its pacing; components/kit/RevealText.tsx
 * draws it.
 *
 *  - Text is split into words, each keeping its trailing space and punctuation, and queued. The network sets what is
 *    available; the queue sets what is shown.
 *  - One loop releases the next word STAGGER_MS after the previous one (about 30–40 ms). Each released word fades
 *    in over FADE_MS (opacity only), so several are mid-fade at once: a soft gradient at the end of the text.
 *  - Adaptive: a backlog shortens the stagger (down to MIN_STAGGER_MS) so the queue catches up within CATCH_UP_MS;
 *    an empty queue just waits; at the end of the stream the rest drains at the fast pace.
 *  - Append only: words already queued never change (a text that does not extend the previous one starts over).
 */
export const FADE_MS = 400;
export const STAGGER_MS = 35;
export const MIN_STAGGER_MS = 9;
export const CATCH_UP_MS = 300;

/** Words with their trailing space ("Hello, ", "world."). */
export function splitWords(text: string): string[] {
  return text.match(/\S+\s*|\s+/g) ?? [];
}

/** How long to wait before releasing the next word, for this many still queued. */
export function staggerFor(queued: number, done: boolean): number {
  if (queued <= 0) return STAGGER_MS;
  if (done) return MIN_STAGGER_MS;
  return Math.max(MIN_STAGGER_MS, Math.min(STAGGER_MS, CATCH_UP_MS / queued));
}

export class RevealQueue {
  /** Every word known, in order (shown or still queued). */
  words: string[] = [];
  /** How many are released (fading in or shown). */
  released = 0;
  private text = '';
  private lastRelease = -Infinity;
  done = false;
  /** Bumped when the text started over (a drawing keyed on it starts over too). */
  gen = 0;

  /** The whole text so far. Returns true when the words changed in a way that is not an append (start over). */
  set(text: string, done = false): boolean {
    this.done = done;
    if (text === this.text) return false;
    if (!text.startsWith(this.text)) {
      this.text = text; this.words = splitWords(text); this.released = 0; this.lastRelease = -Infinity; this.gen++;
      return true;
    }
    // append: the last known word may have grown (a word cut by a chunk), the rest are new
    const tail = splitWords(text.slice(this.text.length));
    if (this.words.length && tail.length && !/\s$/.test(this.words[this.words.length - 1])) {
      const last = this.words.length - 1;
      if (last >= this.released) this.words[last] += tail.shift() as string;   // not shown yet: grows in place
    }
    this.words.push(...tail);
    this.text = text;
    return false;
  }

  /** The pace of the backlog being worked off: the fastest stagger it needed, kept until the queue is empty, so the
   *  tail of a burst does not slow down as it shrinks (all of it shows within CATCH_UP_MS). */
  private pace = STAGGER_MS;

  /** Release what is due at `now` (ms). Returns how many were released this call. */
  tick(now: number): number {
    let n = 0;
    while (this.released < this.words.length) {
      const queued = this.words.length - this.released;
      this.pace = Math.min(this.pace, staggerFor(queued, this.done));
      if (now - this.lastRelease < this.pace) break;
      this.lastRelease = this.lastRelease === -Infinity || now - this.lastRelease > STAGGER_MS * 4 ? now : this.lastRelease + this.pace;
      this.released++;
      n++;
    }
    if (this.released >= this.words.length) this.pace = STAGGER_MS;   // caught up: the next words at the usual pace
    return n;
  }

  /** Show everything at once (scrolled away, cancelled, reduced motion). */
  flush(): void { this.released = this.words.length; }
}
