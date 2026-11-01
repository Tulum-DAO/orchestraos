/**
 * CallWaveform — the call bar's rolling waveform (lib/callWave.ts has the rules): one row of bars in the agent's
 * colour, your voice full colour and the agent's in a muted shade of it, new bars entering on the right, the oldest
 * tapering to dots, silence a dotted baseline flowing through.
 *
 * One requestAnimationFrame loop reads both analysers and writes ONLY each bar's height and opacity, straight onto
 * the elements (no re-render per frame), so it holds 60 fps. Each frame's own work time is kept in
 * window.__orchestraCallWave (frames, the slowest, how many over 16 ms) so a performance check can read it.
 */
import { useEffect, useRef } from 'react';
import { barLook, levelOf, RollingTimeline, SLOTS, MIN_H } from '../../lib/callWave';
import type { CallLevels } from '../../stores/callStore';

export interface WaveStats { frames: number; maxMs: number; over16: number; worstGapMs: number; gapsOver17: number }

export default function CallWaveform({ levels, color, muted }: { levels: CallLevels; color: string; muted: string }) {
  const userRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const agentRefs = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    const tl = new RollingTimeline();
    let raf = 0;
    let micBuf: Uint8Array | null = null;
    let playBuf: Uint8Array | null = null;
    let last = 0;
    const stats: WaveStats = { frames: 0, maxMs: 0, over16: 0, worstGapMs: 0, gapsOver17: 0 };
    (window as unknown as { __orchestraCallWave?: WaveStats }).__orchestraCallWave = stats;
    const read = (a: AnalyserNode | null, buf: Uint8Array | null): [number, Uint8Array | null] => {
      if (!a) return [0, buf];
      const b = buf && buf.length === a.fftSize ? buf : new Uint8Array(a.fftSize);
      a.getByteTimeDomainData(b as Uint8Array<ArrayBuffer>);
      return [levelOf(b), b];
    };
    const frame = (now: number) => {
      const t0 = performance.now();
      let mic: number, play: number;
      [mic, micBuf] = read(levels.mic(), micBuf);
      [play, playBuf] = read(levels.playback(), playBuf);
      tl.push(now, mic, play);
      for (let i = 0; i < SLOTS; i++) {
        const s = tl.slots[i];
        const u = barLook(i, s.user, tl.slice), a = barLook(i, s.agent, tl.slice);
        const ue = userRefs.current[i], ae = agentRefs.current[i];
        // height and opacity only
        if (ue) { ue.style.height = `${u.h}px`; ue.style.opacity = s.user >= s.agent || s.agent < 0.035 ? `${u.o}` : '0'; }
        if (ae) { ae.style.height = `${a.h}px`; ae.style.opacity = s.agent > s.user ? `${a.o}` : '0'; }
      }
      const took = performance.now() - t0;
      stats.frames++;
      if (took > stats.maxMs) stats.maxMs = took;
      if (took > 16) stats.over16++;
      if (last && stats.frames > 2) { stats.worstGapMs = Math.max(stats.worstGapMs, now - last); if (now - last > 17.5) stats.gapsOver17++; }
      last = now;
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [levels]);

  return (
    <div className="flex h-[30px] w-full min-w-0 shrink-0 items-center justify-between gap-[3px] overflow-hidden" aria-hidden data-call-waveform>
      {Array.from({ length: SLOTS }, (_, i) => (
        <span key={i} className="relative flex h-full w-[3px] shrink-0 items-center justify-center">
          <span ref={(el) => { agentRefs.current[i] = el; }} className="absolute w-[3px] rounded-full"
                style={{ height: MIN_H, opacity: 0, background: muted }} data-wave-agent />
          <span ref={(el) => { userRefs.current[i] = el; }} className="absolute w-[3px] rounded-full"
                style={{ height: MIN_H, opacity: 0.3, background: color }} data-wave-user />
        </span>
      ))}
    </div>
  );
}
