/**
 * useDictation — the Mic button's zero-key dictation, shared by EVERY Arturo composer
 * (home page + the "Ask Arturo" pill) so the buttons behave identically everywhere.
 * On-device Web Speech API (Chrome/Edge/Safari), no vendor key, no WebSocket. Partial
 * text live-updates the draft; finals append; typed text before the tap is kept.
 * Browsers without a recognizer get an honest note (never a dead button).
 */
import { useEffect, useRef, useState } from 'react';
import { startDictation, mergeDictation, DICTATION_UNAVAILABLE, type DictationHandle } from '../../lib/dictation.ts';

export function useDictation(draft: string, setDraft: (v: string) => void, onStarted?: () => void) {
  const [dictating, setDictating] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const handle = useRef<DictationHandle | null>(null);
  const base = useRef('');                 // what was typed before the mic was tapped
  const committed = useRef<string[]>([]);

  function stop() {
    handle.current?.stop();
    handle.current = null;
    setDictating(false);
  }
  function toggle() {
    setNote(null);
    if (dictating) { stop(); return; }
    base.current = draft;
    committed.current = [];
    const h = startDictation({
      onPartial: (text) => setDraft(mergeDictation(base.current, committed.current, text)),
      onFinal: (text) => {
        committed.current = [...committed.current, text];
        setDraft(mergeDictation(base.current, committed.current, ''));
      },
      onEnd: () => { handle.current = null; setDictating(false); },   // silence timeout / tab hidden
      onError: (code) => {
        if (code === 'no-speech' || code === 'aborted') return;       // ordinary; onEnd follows
        setNote(code === 'not-allowed' || code === 'service-not-allowed'
          ? 'microphone permission was denied — allow the mic for this site and tap again'
          : `dictation error: ${code}`);
      },
    });
    if (!h) { setNote(DICTATION_UNAVAILABLE); return; }
    handle.current = h;
    setDictating(true);
    onStarted?.();
  }
  useEffect(() => () => { handle.current?.stop(); }, []);
  return { dictating, note, toggle, stop, clearNote: () => setNote(null) };
}
