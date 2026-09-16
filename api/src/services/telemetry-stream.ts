/**
 * telemetry-stream.ts — the transport-agnostic delta producer (the WS-READY
 * SEAM). The SSE route adapts open()/tick() to res.write(); a LATER WebSocket
 * route adapts the SAME controller with zero rewrite. Ship SSE only now.
 *
 * The controller is where the API's court gate lives (defense-in-depth backstop
 * to snapshot.py's primary write-gate):
 *   - open(): resolve session -> lineage_root (BLOCKING-1: null => block), call
 *     B1's flag seam once (telemetry-court). Blocked => ONE block frame, then
 *     the stream emits no deltas ever.
 *   - tick(): relay new clean frames from the court-gated delta log; on a SLOW
 *     re-check cadence, re-run the flag seam so a mid-stream clean->flagged flip
 *     drops to block (rider i). The per-tick hot path is pure TS (no subprocess).
 *
 * Because the delta log is ALREADY court-gated at write time (a flagged lineage
 * appends zero bytes), the tick relay cannot surface flagged content even
 * between slow re-checks — the re-check is the backstop, the write-gate the
 * guarantee.
 */
import { flagStatus, shouldBlock } from './telemetry-court.js';
import { resolveLineageRoot, readDeltaFrames, type DeltaFrame } from './telemetry-store.js';

export type StreamFrame =
  | { type: 'block'; reason: string }
  | { type: 'delta'; frames: DeltaFrame[]; lastSeq: number }
  | { type: 'heartbeat' };

export class DeltaStreamController {
  private blocked = false;
  private lastSeq: number;
  private ticks = 0;
  private readonly recheckEvery: number;

  constructor(private session: string, opts: { afterSeq?: number; recheckEveryTicks?: number } = {}) {
    this.lastSeq = opts.afterSeq ?? 0;
    this.recheckEvery = opts.recheckEveryTicks ?? 10;   // ~10 ticks between slow re-checks
  }

  get isBlocked(): boolean { return this.blocked; }

  /** First frame: a block (flagged / unresolved / fail-closed) OR the initial
   *  backlog of clean deltas. Court-gates via B1's seam. */
  async open(): Promise<StreamFrame> {
    const root = resolveLineageRoot(this.session);
    const fs = await flagStatus(root);
    if (shouldBlock(fs)) {
      this.blocked = true;
      const reason = !root ? 'unresolved-lineage' : fs.flagged ? 'flagged' : 'fail-closed';
      return { type: 'block', reason };
    }
    const frames = readDeltaFrames(this.session, this.lastSeq);
    if (frames.length) this.lastSeq = frames[frames.length - 1].seq;
    return { type: 'delta', frames, lastSeq: this.lastSeq };
  }

  /** One beat. null => nothing new (route may emit a heartbeat). A block frame
   *  is terminal — after it, tick() always returns null. */
  async tick(): Promise<StreamFrame | null> {
    if (this.blocked) return null;
    this.ticks++;
    if (this.ticks % this.recheckEvery === 0) {
      const root = resolveLineageRoot(this.session);
      const fs = await flagStatus(root);
      if (shouldBlock(fs)) {
        this.blocked = true;
        return { type: 'block', reason: 'mid-stream-flip' };
      }
    }
    const frames = readDeltaFrames(this.session, this.lastSeq);
    if (!frames.length) return null;
    this.lastSeq = frames[frames.length - 1].seq;
    return { type: 'delta', frames, lastSeq: this.lastSeq };
  }
}
