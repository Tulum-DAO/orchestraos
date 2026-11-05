/**
 * reveal.ts — the word-by-word reveal (SMOOTH_STREAM_SPEC.md), shared by every place that shows words arriving: the
 * live call's transcript and the agent chat's streamed reply. Pure: the queue and its pacing; components/kit/RevealText.tsx
 * (plain words) and components/kit/StreamMarkdown.tsx (a streamed reply's Markdown) draw it.
 *
 *  - Text is split into words, each keeping its trailing space and punctuation, and queued. The network sets what is
 *    available; the queue sets what is shown.
 *  - One loop releases the next word STAGGER_MS after the previous one (about 30–40 ms). Each released word fades
 *    in over FADE_MS (opacity only), so several are mid-fade at once: a soft gradient at the end of the text.
 *  - Adaptive: a backlog shortens the stagger so the queue catches up within CATCH_UP_MS (a burst of up to ~30 words
 *    at MIN_STAGGER_MS or slower; a bigger one shorter still, several words a frame, so the newest word shown is never
 *    more than CATCH_UP_MS behind what has arrived); an empty queue just waits; at the end of the stream the rest
 *    drains at the fast pace.
 *  - Append only: words already queued never change (a text that does not extend the previous one starts over).
 *
 * RevealPacer is the pacing alone, over a count of units (a streamed reply's units are its rendered words, code lines
 * and tables, not the raw text); RevealQueue is the pacer over a plain text's words.
 */
export const FADE_MS = 400;
export const STAGGER_MS = 35;
export const MIN_STAGGER_MS = 9;
export const CATCH_UP_MS = 300;
/** The backlog is paced to be out within this: CATCH_UP_MS less two frames, one for the words to be drawn after they
 *  arrive and one for the frame the last of them is released on. */
const CATCH_UP_PACE_MS = CATCH_UP_MS - 34;

/** Words with their trailing space ("Hello, ", "world."). */
export function splitWords(text: string): string[] {
  return text.match(/\S+\s*|\s+/g) ?? [];
}

/** How long to wait before releasing the next word, for this many still queued. A backlog is worked off within
 *  CATCH_UP_MS (at most STAGGER_MS apart); at the end of the stream the rest drains at MIN_STAGGER_MS, or faster when
 *  even that would take longer than CATCH_UP_MS. */
export function staggerFor(queued: number, done: boolean): number {
  if (queued <= 0) return STAGGER_MS;
  const catchUp = CATCH_UP_PACE_MS / queued;
  if (done) return Math.min(MIN_STAGGER_MS, catchUp);
  return Math.min(STAGGER_MS, catchUp);
}

export class RevealPacer {
  /** How many units are known (shown or still queued). */
  known = 0;
  /** How many are released (fading in or shown). */
  released = 0;
  done = false;
  /** When each released unit was released (the tick clock, ms), so a drawing mounted later can carry on its fade. */
  releasedAt: number[] = [];
  private lastRelease = -Infinity;
  /** The pace of the backlog being worked off: the fastest stagger it needed, kept until the queue is empty, so the
   *  tail of a burst does not slow down as it shrinks (all of it shows within CATCH_UP_MS). */
  private pace = STAGGER_MS;

  /** How many units there are now, and whether the stream has ended. Units never shrink below what is released. */
  setKnown(n: number, done = false): void {
    this.known = Math.max(n, this.released);
    this.done = done;
  }

  /** Release what is due at `now` (ms). Returns how many were released this call. */
  tick(now: number): number {
    let n = 0;
    while (this.released < this.known) {
      const queued = this.known - this.released;
      this.pace = Math.min(this.pace, staggerFor(queued, this.done));
      if (now - this.lastRelease < this.pace) break;
      this.lastRelease = this.lastRelease === -Infinity || now - this.lastRelease > STAGGER_MS * 4 ? now : this.lastRelease + this.pace;
      this.releasedAt[this.released] = now;
      this.released++;
      n++;
    }
    if (this.released >= this.known) this.pace = STAGGER_MS;   // caught up: the next words at the usual pace
    return n;
  }

  /** Show everything at once (scrolled away, cancelled, reduced motion). Flushed units count as long shown. */
  flush(now = -Infinity): void {
    for (let i = this.released; i < this.known; i++) this.releasedAt[i] = now;
    this.released = this.known;
  }

  /** Start over (the text was replaced, not extended). */
  reset(): void {
    this.known = 0; this.released = 0; this.done = false; this.releasedAt = []; this.lastRelease = -Infinity; this.pace = STAGGER_MS;
  }
}

export class RevealQueue extends RevealPacer {
  /** Every word known, in order (shown or still queued). */
  words: string[] = [];
  private text = '';
  /** Bumped when the text started over (a drawing keyed on it starts over too). */
  gen = 0;

  /** The whole text so far. Returns true when the words changed in a way that is not an append (start over). */
  set(text: string, done = false): boolean {
    if (text === this.text) { this.setKnown(this.words.length, done); return false; }
    if (!text.startsWith(this.text)) {
      this.reset();
      this.text = text; this.words = splitWords(text); this.gen++;
      this.setKnown(this.words.length, done);
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
    this.setKnown(this.words.length, done);
    return false;
  }

  /** Show everything at once (scrolled away, cancelled, reduced motion). */
  flush(now = -Infinity): void { this.setKnown(this.words.length, this.done); super.flush(now); }
}
