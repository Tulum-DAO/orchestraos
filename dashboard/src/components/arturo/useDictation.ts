/**
 * useDictation — the Mic button's zero-key dictation, shared by EVERY Arturo composer
 * (home page + the "Ask Arturo" pill) so the buttons behave identically everywhere.
 *
 * Three tiers, chosen per tap and by EFFECT (item C, DEC-1790045383668733):
 *   1. on-device Web Speech (Chrome/Edge/Safari): words appear live while you talk.
 *   2. record with MediaRecorder, transcribe on the box (local Whisper, no key): for browsers
 *      with no recognizer, and for ones that have the API but no backend (Brave, keyless
 *      Chromium — they error `network` / `service-not-allowed` on the same tap, so we fall
 *      through without the user tapping again).
 *   3. neither: an honest note naming the fix. The button is never silently dead.
 * Typed text before the tap is kept; finals append; the interim segment is replaced live.
 */
import { useEffect, useRef, useState } from 'react';
import {
  startDictation, mergeDictation, DICTATION_UNAVAILABLE, isTier1DeadError,
  startRecording, transcribeBlob, transcribeReason, recordingBlockedReason, speechRecognitionCtor, blobToWav16k,
  type DictationHandle, type RecordingHandle,
} from '../../lib/dictation.ts';

export type DictationMode = 'idle' | 'listening' | 'recording' | 'transcribing';

export function useDictation(draft: string, setDraft: (v: string) => void, onStarted?: () => void) {
  const [mode, setMode] = useState<DictationMode>('idle');
  const [note, setNote] = useState<string | null>(null);
  const handle = useRef<DictationHandle | null>(null);
  const recorder = useRef<RecordingHandle | null>(null);
  const base = useRef('');                 // what was typed before the mic was tapped
  const committed = useRef<string[]>([]);
  const gotResult = useRef(false);
  const active = useRef(false);            // false after stop(): late recognizer results are dropped
  const draftRef = useRef(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);   // read in the tap handler, never during render

  const stopWanted = useRef(false);
  function stop() {
    active.current = false;               // FIRST: Chrome fires the pending final result after stop()
    handle.current?.stop();
    handle.current = null;
    if (recorder.current && mode === 'recording' && stopWanted.current) { recorder.current.stop(); recorder.current = null; return; }   // onClip -> transcribing
    if (recorder.current) { recorder.current.stop(); recorder.current = null; }
    setMode('idle');
  }

  async function startTier2() {
    const blocked = recordingBlockedReason();
    if (blocked) { setNote(blocked); setMode('idle'); return; }
    const rec = await startRecording({
      onClip: async (blob) => {
        recorder.current = null;
        if (!active.current) { setMode('idle'); return; }   // cancelled by a send
        setMode('transcribing');
        const wav = await blobToWav16k(blob);          // 16 kHz WAV for the default engine; original if undecodable
        const r = await transcribeBlob(wav || blob);
        if (r.ok && r.text) {
          committed.current = [...committed.current, r.text];
          setDraft(mergeDictation(base.current, committed.current, ''));
        } else {
          setNote(transcribeReason(r));
        }
        setMode('idle');
      },
      onError: (reason) => { recorder.current = null; setNote(reason); setMode('idle'); },
    });
    if (!rec) { setMode('idle'); return; }
    recorder.current = rec;
    setMode('recording');
    onStarted?.();
  }

  function toggle() {
    setNote(null);
    if (mode === 'transcribing') return;
    if (mode !== 'idle') { stopWanted.current = true; active.current = mode === 'recording'; stop(); stopWanted.current = false; return; }
    base.current = draftRef.current;
    committed.current = [];
    gotResult.current = false;
    if (!speechRecognitionCtor()) { void startTier2(); return; }
    active.current = true;
    const h = startDictation({
      onPartial: (text) => { if (!active.current) return; gotResult.current = true; setDraft(mergeDictation(base.current, committed.current, text)); },
      onFinal: (text) => {
        if (!active.current) return;        // sent already: the box stays cleared
        gotResult.current = true;
        committed.current = [...committed.current, text];
        setDraft(mergeDictation(base.current, committed.current, ''));
      },
      onEnd: () => { if (handle.current) { handle.current = null; setMode('idle'); } },   // silence timeout / tab hidden
      onError: (code) => {
        if (code === 'no-speech' || code === 'aborted') return;       // ordinary; onEnd follows
        if (isTier1DeadError(code) && !gotResult.current) {          // API present, backend dead -> tier 2, same tap
          handle.current?.stop(); handle.current = null;
          void startTier2();
          return;
        }
        setNote(code === 'not-allowed' || code === 'service-not-allowed'
          ? 'microphone permission was denied — allow the mic for this site and tap again'
          : `dictation error: ${code}`);
      },
    });
    if (!h) { active.current = false; setNote(DICTATION_UNAVAILABLE); return; }
    handle.current = h;
    setMode('listening');
    onStarted?.();
  }
  useEffect(() => () => { handle.current?.stop(); recorder.current?.stop(); }, []);
  return { mode, dictating: mode !== 'idle', note, toggle, stop, clearNote: () => setNote(null) };
}
