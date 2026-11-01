/**
 * RevealText — words that fade in one after another as they arrive (lib/reveal.ts; SMOOTH_STREAM_SPEC.md): the
 * call's live transcript today, the streamed reply next. Every word is rendered at its final place from the moment
 * it is known (transparent), so lines wrap once and nothing reflows; one requestAnimationFrame loop releases them, and
 * a released word only gains a class that runs its CSS opacity fade. Words already shown are never re-rendered.
 * Reduced motion: words appear without the fade.
 */
import { useEffect, useRef, useState } from 'react';
import { RevealQueue } from '../../lib/reveal';
import './reveal.css';

/** Each reveal frame's own work, kept in window.__orchestraReveal so a performance check can read it. */
interface RevealStats { frames: number; maxMs: number; over16: number }
function revealStats(): RevealStats {
  const w = window as unknown as { __orchestraReveal?: RevealStats };
  return (w.__orchestraReveal ||= { frames: 0, maxMs: 0, over16: 0 });
}

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

export default function RevealText({ text, done = false, className }: { text: string; done?: boolean; className?: string }) {
  // The queue lives with this text's drawing; its words are read in render (append-only, so nothing shown changes).
  const [q] = useState(() => new RevealQueue());
  q.set(text, done);
  const gen = q.gen;
  const words = q.words.slice();
  const spans = useRef<(HTMLSpanElement | null)[]>([]);
  useEffect(() => {
    let raf = 0;
    let shown = 0;
    // words already switched on (this effect re-runs as text grows): keep them on, without fading again
    while (shown < words.length && spans.current[shown]?.classList.contains('is-on')) shown++;
    if (reducedMotion()) q.flush();
    const stats = revealStats();
    const frame = (now: number) => {
      const t0 = performance.now();
      q.tick(now);
      while (shown < q.released) { spans.current[shown]?.classList.add('is-on'); shown++; }
      const took = performance.now() - t0;
      stats.frames++; if (took > stats.maxMs) stats.maxMs = took; if (took > 16) stats.over16++;
      if (q.released < q.words.length || !q.done) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [q, text, done, gen, words.length]);
  return (
    <span className={className} data-reveal key={gen}>
      {words.map((w, i) => (
        <span key={i} ref={(el) => { spans.current[i] = el; }} className="reveal-w" data-reveal-word={i}>{w}</span>
      ))}
    </span>
  );
}
