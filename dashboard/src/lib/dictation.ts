/**
 * dictation — on-device speech-to-text into a text box, no vendor key.
 *
 * Uses the browser's Web Speech API (window.SpeechRecognition ||
 * webkitSpeechRecognition — Chrome/Edge). The gateway never transcribes the
 * caller's own voice, so this is the ONLY way words get from the microphone
 * into the composer on a zero-key install. It is deliberately independent of
 * VoiceSession (the ElevenLabs/Hume CALL path): no WebSocket, no vendor flag.
 *
 * Two parts: `mergeDictation` is pure (unit-tested) and decides what the text
 * box shows; `startDictation` wraps the recognizer and returns a stop handle.
 */

export const DICTATION_UNAVAILABLE =
  'dictation needs on-device speech recognition (Chrome or Edge) — this browser has none, ' +
  'and the gateway itself doesn\'t transcribe your own voice, only Arturo\'s replies';

/** Minimal shape of the parts of the Web Speech API this file touches
 * (lib.dom's SpeechRecognition typings are not universally present in every
 * TS/DOM lib config, so we declare just what we use). */
export interface RecognitionAlternativeLike { transcript: string }
export interface RecognitionResultLike { readonly isFinal?: boolean; readonly length: number; [index: number]: RecognitionAlternativeLike | undefined }
export interface RecognitionResultEventLike { resultIndex?: number; results?: ArrayLike<RecognitionResultLike> }
export interface RecognitionErrorEventLike { error?: string }
export interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((ev: RecognitionResultEventLike) => void) | null;
  onerror: ((ev: RecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;
type WindowWithSpeech = { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };

export function speechRecognitionCtor(): SpeechRecognitionCtor | null {
  const w = (typeof window === 'undefined' ? undefined : window) as WindowWithSpeech | undefined;
  if (!w) return null;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function dictationSupported(): boolean {
  return speechRecognitionCtor() !== null;
}

/**
 * What the text box should contain while dictating:
 *   base      — what was already typed when the mic was tapped (kept verbatim)
 *   committed — final segments so far, in order
 *   partial   — the current interim (not yet final) segment, replaced on each update
 * Segments are joined with single spaces; a partial that becomes final replaces
 * itself rather than duplicating.
 */
export function mergeDictation(base: string, committed: string[], partial: string): string {
  const parts = [base.replace(/\s+$/, ''), ...committed.map((s) => s.trim()), partial.trim()]
    .filter((s) => s.length > 0);
  return parts.join(' ');
}

export interface DictationCallbacks {
  /** Interim text for the segment currently being spoken (replaces the previous partial). */
  onPartial?: (text: string) => void;
  /** A segment the recognizer is done with. */
  onFinal?: (text: string) => void;
  /** The recognizer stopped on its own (silence timeout, tab hidden, stop()). */
  onEnd?: () => void;
  /** Recognizer error code, e.g. 'not-allowed' (mic permission), 'no-speech', 'network'. */
  onError?: (code: string) => void;
}

export interface DictationHandle { stop(): void; }

/** Routes one SpeechRecognition `result` event to onPartial/onFinal. Exported for tests. */
export function routeRecognitionEvent(ev: RecognitionResultEventLike, cb: DictationCallbacks): void {
  const results = ev?.results;
  if (!results) return;
  for (let i = ev.resultIndex ?? 0; i < results.length; i++) {
    const r = results[i];
    const text: string = r?.[0]?.transcript ?? '';
    if (!text) continue;
    if (r?.isFinal) cb.onFinal?.(text);
    else cb.onPartial?.(text);
  }
}

/**
 * Start listening. Returns null (and calls nothing) when the browser has no
 * recognizer — the caller shows DICTATION_UNAVAILABLE. Otherwise returns a
 * handle; `stop()` ends the session and `onEnd` still fires from the recognizer.
 */
export function startDictation(cb: DictationCallbacks, lang = 'en-US'): DictationHandle | null {
  const Ctor = speechRecognitionCtor();
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.continuous = true;
  rec.interimResults = true;
  rec.lang = lang;
  rec.onresult = (ev) => routeRecognitionEvent(ev, cb);
  rec.onerror = (ev) => { cb.onError?.(ev?.error || 'unknown'); };
  rec.onend = () => { cb.onEnd?.(); };
  rec.start();
  return { stop: () => { try { rec.stop(); } catch { /* already stopped */ } } };
}
