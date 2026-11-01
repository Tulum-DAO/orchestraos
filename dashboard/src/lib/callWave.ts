/**
 * callWave.ts — the call bar's waveform as a rolling timeline (components/call/CallWaveform.tsx draws it).
 *
 * ONE row of bars. Time is cut into short slices (SLICE_MS); each slice keeps the loudest level heard in it from
 * your microphone and from the agent's playback. The newest slice is the rightmost bar, and every slice moves one bar
 * left as the next begins, so new bars enter on the right and scroll left. Toward the left edge the bars taper: they
 * shrink and fade until the oldest are dots. Silence is a dotted baseline: a slice with nothing heard is a dot, and
 * the dots alternate in strength by slice, so the line visibly flows left with the rest.
 *
 * The drawing only ever changes each bar's height and opacity (never its position or colour): your voice is one
 * layer in the agent's colour, the agent's voice a second layer in a muted shade of it. Pure, so it is tested
 * without audio.
 */
export const SLICE_MS = 60;
export const SLOTS = 44;
/** How many bars at the left edge taper down to dots. */
export const TAPER_SLOTS = 12;
/** A level under this is silence. */
export const SILENCE = 0.035;
/** A bar's height range, px. A dot is MIN_H high (and as wide). */
export const MIN_H = 3;
export const MAX_H = 26;

/** Loudness 0..1 from an analyser's time-domain bytes (128 = silence): the RMS, with a curve that lifts quiet
 *  speech so it reads. */
export function levelOf(bytes: Uint8Array): number {
  let sum = 0;
  for (let i = 0; i < bytes.length; i++) { const v = (bytes[i] - 128) / 128; sum += v * v; }
  const rms = Math.sqrt(sum / Math.max(1, bytes.length));
  return Math.min(1, Math.pow(rms * 3.2, 0.7));
}

export interface Slot { user: number; agent: number }

/** The timeline: slices of the loudest levels heard, newest last. */
export class RollingTimeline {
  readonly slots: Slot[] = Array.from({ length: SLOTS }, () => ({ user: 0, agent: 0 }));
  /** Slices since the call began: the silence dots' alternation follows it, so the line flows. */
  slice = 0;
  private sliceStart = -1;
  private cur: Slot = { user: 0, agent: 0 };

  /** A reading at `now` ms. Returns true when a slice closed (every bar moved one place left). */
  push(now: number, user: number, agent: number): boolean {
    if (this.sliceStart < 0) this.sliceStart = now;
    let moved = false;
    // close the slices that have ended first: a reading belongs to the slice it falls in
    while (now - this.sliceStart >= SLICE_MS) {
      this.slots.shift();
      this.slots.push(this.cur);
      this.cur = { user: 0, agent: 0 };
      this.sliceStart += SLICE_MS;
      this.slice++;
      moved = true;
    }
    this.cur.user = Math.max(this.cur.user, user);
    this.cur.agent = Math.max(this.cur.agent, agent);
    return moved;
  }
}

export interface BarLook { h: number; o: number }

/** How bar `i` (0 = the oldest, at the left) is drawn for a level: its height and opacity. The taper at the left
 *  edge, and silence as a flowing dot. */
export function barLook(i: number, level: number, slice: number): BarLook {
  const t = Math.min(1, i / TAPER_SLOTS);                 // 0 at the left edge, 1 past the taper
  const ease = t * t * (3 - 2 * t);
  if (level < SILENCE) {
    // the dotted baseline: alternate dots by slice, so the pattern moves left one bar per slice
    const strong = (i + slice) % 2 === 0;
    return { h: MIN_H, o: (strong ? 0.55 : 0.22) * (0.35 + 0.65 * ease) };
  }
  const h = MIN_H + (MAX_H - MIN_H) * level * ease;
  return { h: Math.max(MIN_H, Math.round(h * 10) / 10), o: 0.3 + 0.7 * ease };
}
