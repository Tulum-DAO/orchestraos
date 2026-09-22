/**
 * dictation — on-device speech-to-text into a text box, no vendor key.
 *
 * Tier 1: the browser's Web Speech API (window.SpeechRecognition ||
 * webkitSpeechRecognition — Chrome/Edge/Safari), live interim words.
 * Tier 2 (item C): no recognizer, or it fails by EFFECT (Brave / keyless
 * Chromium expose the constructor and then error `network` /
 * `service-not-allowed`) -> record with MediaRecorder and POST the clip to
 * /api/arturo/transcribe, where the box transcribes it with a local Whisper
 * model. No vendor key anywhere. Deliberately independent of VoiceSession
 * (the ElevenLabs/Hume CALL path): no WebSocket, no vendor flag.
 *
 * Pure parts (`mergeDictation`, `routeRecognitionEvent`, `pickRecordingMime`,
 * `isTier1DeadError`, `transcribeReason`) are unit-tested; the two wrappers
 * (`startDictation`, `startRecording`) return stop handles.
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

// ─── tier 2: record on the browser, transcribe on the box ──────────────────────────────

/** Tier-1 error codes that mean "this browser has the API but no working backend"
 *  (Brave, Chromium without Google keys, Chrome offline). Same tap falls to tier 2. */
export function isTier1DeadError(code: string): boolean {
  return code === 'network' || code === 'service-not-allowed' || code === 'language-not-supported';
}

/** The first MediaRecorder mime this browser can produce, in the order the box decodes best. */
export function pickRecordingMime(isSupported: (t: string) => boolean = (t) =>
  typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(t)): string {
  for (const t of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/mpeg']) {
    try { if (isSupported(t)) return t; } catch { /* old MediaRecorder without isTypeSupported */ }
  }
  return '';
}

export function recordingSupported(): boolean {
  return typeof MediaRecorder !== 'undefined' && typeof navigator !== 'undefined'
    && !!navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function';
}

/** Why recording cannot start here, in first person; null when it can. Secure-context gate lives
 *  HERE (in front of tier 2 only): tier 1 can work over plain http in Chrome. */
export function recordingBlockedReason(win: { isSecureContext?: boolean } = (typeof window === 'undefined' ? {} : window)): string | null {
  if (win.isSecureContext === false) return 'the microphone needs HTTPS (or localhost) — this page is plain http, so no browser will open the mic';
  if (!recordingSupported()) return 'this browser cannot record audio (no MediaRecorder / getUserMedia)';
  return null;
}

export const MAX_RECORDING_MS = 60_000;

export interface RecordingHandle { stop(): void; mimeType: string }

/**
 * Start recording the microphone. Resolves once the stream is open (the button flips to
 * "recording"); `onClip` fires with the blob after stop() or the 60 s cap; `onError` with a
 * first-person reason (permission denied, no mic…).
 */
export async function startRecording(cb: { onClip: (blob: Blob) => void; onError: (reason: string) => void }): Promise<RecordingHandle | null> {
  const blocked = recordingBlockedReason();
  if (blocked) { cb.onError(blocked); return null; }
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e: unknown) {
    const name = (e as { name?: string })?.name || '';
    cb.onError(name === 'NotAllowedError' || name === 'SecurityError'
      ? 'microphone permission was denied — allow the mic for this site and tap again'
      : name === 'NotFoundError' ? 'no microphone was found on this device' : `could not open the microphone (${name || 'unknown'})`);
    return null;
  }
  const mimeType = pickRecordingMime();
  const rec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  let done = false;
  rec.ondataavailable = (ev: BlobEvent) => { if (ev.data && ev.data.size > 0) chunks.push(ev.data); };
  rec.onstop = () => {
    if (done) return;
    done = true;
    stream.getTracks().forEach((t) => t.stop());
    cb.onClip(new Blob(chunks, { type: rec.mimeType || mimeType || 'audio/webm' }));
  };
  rec.onerror = () => { cb.onError('recording failed'); };
  rec.start();
  const cap = setTimeout(() => { try { if (rec.state !== 'inactive') rec.stop(); } catch { /* already stopped */ } }, MAX_RECORDING_MS);
  return {
    mimeType: rec.mimeType || mimeType,
    stop: () => { clearTimeout(cap); try { if (rec.state !== 'inactive') rec.stop(); } catch { /* already stopped */ } },
  };
}

// ─── WAV in the browser (P1-a) ─────────────────────────────────────────────────────────────
// The default server engine takes 16 kHz mono 16-bit PCM WAV, so the browser decodes its own
// MediaRecorder clip (webm/opus in Chrome/Firefox, mp4/aac in Safari) and resamples it here.
// No ffmpeg anywhere. 60 s -> 1.9 MB. Pure parts are unit-tested.

export const WAV_RATE = 16000;

/** Float32 samples (-1..1) -> RIFF/WAVE 16-bit PCM mono at `rate`. */
export function encodeWav(samples: Float32Array, rate: number = WAV_RATE): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o: number, t: string) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE');
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

/** Mix channels to mono. */
export function toMono(channels: Float32Array[]): Float32Array {
  if (channels.length === 1) return channels[0];
  const n = channels[0].length; const out = new Float32Array(n);
  for (const ch of channels) for (let i = 0; i < n; i++) out[i] += ch[i] / channels.length;
  return out;
}

/** Pure-TS resampler (windowed-sinc, 8 taps each side) for browsers whose OfflineAudioContext
 *  refuses a 16 kHz render (old Safari). Good enough for speech; not used on the fast path. */
export function resampleSinc(input: Float32Array, from: number, to: number, taps = 16): Float32Array {
  if (from === to) return input;
  const ratio = from / to; const n = Math.round(input.length / ratio); const out = new Float32Array(n);
  const cutoff = Math.min(1, to / from);            // anti-alias at the new Nyquist when downsampling
  for (let i = 0; i < n; i++) {
    const center = i * ratio; const lo = Math.max(0, Math.floor(center) - taps), hi = Math.min(input.length - 1, Math.floor(center) + taps);
    let acc = 0, wsum = 0;
    for (let j = lo; j <= hi; j++) {
      const t = j - center; const x = cutoff * t;
      const sinc = x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x);
      const w = 0.5 + 0.5 * Math.cos(Math.PI * t / (taps + 1));            // Hann window
      acc += input[j] * sinc * w; wsum += sinc * w;
    }
    out[i] = wsum !== 0 ? acc / wsum : 0;
  }
  return out;
}

/** Decode a recorded clip to 16 kHz mono WAV. Fast path: OfflineAudioContext renders at 16 kHz
 *  directly (no user gesture needed; it runs after the recording stopped). Fallback: decode at the
 *  native rate with a plain AudioContext and resample in TS. Returns null when the browser cannot
 *  decode the container at all — the caller then uploads the original blob (the opt-in
 *  faster-whisper engine can still decode it; the default engine answers wav_required). */
export async function blobToWav16k(blob: Blob): Promise<Blob | null> {
  const bytes = await blob.arrayBuffer();
  const Offline: typeof OfflineAudioContext | undefined = (window as unknown as { OfflineAudioContext?: typeof OfflineAudioContext; webkitOfflineAudioContext?: typeof OfflineAudioContext }).OfflineAudioContext
    || (window as unknown as { webkitOfflineAudioContext?: typeof OfflineAudioContext }).webkitOfflineAudioContext;
  const Ctx: typeof AudioContext | undefined = (window as unknown as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).AudioContext
    || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  // 1. decode at native rate (any context can decode)
  let decoded: AudioBuffer | null = null;
  try {
    const probe = Offline ? new Offline(1, 1, 44100) : Ctx ? new Ctx() : null;
    if (!probe) return null;
    decoded = await new Promise<AudioBuffer>((res, rej) => { const p = probe.decodeAudioData(bytes.slice(0), res, rej); if (p && typeof (p as Promise<AudioBuffer>).then === 'function') (p as Promise<AudioBuffer>).then(res, rej); });
    if ('close' in probe && typeof (probe as AudioContext).close === 'function') void (probe as AudioContext).close();
  } catch { return null; }
  if (!decoded || decoded.length === 0) return null;
  const channels: Float32Array[] = []; for (let c = 0; c < decoded.numberOfChannels; c++) channels.push(decoded.getChannelData(c));
  const mono = toMono(channels);
  // 2. resample to 16 kHz: OfflineAudioContext render (fast path) or pure TS
  try {
    if (Offline) {
      const n = Math.ceil(mono.length * WAV_RATE / decoded.sampleRate);
      const off = new Offline(1, n, WAV_RATE);
      const src = off.createBufferSource(); const b = off.createBuffer(1, mono.length, decoded.sampleRate); b.copyToChannel(new Float32Array(mono), 0);
      src.buffer = b; src.connect(off.destination); src.start(0);
      const rendered = await off.startRendering();
      return encodeWav(rendered.getChannelData(0), WAV_RATE);
    }
  } catch { /* old Safari: fall through */ }
  return encodeWav(resampleSinc(mono, decoded.sampleRate, WAV_RATE), WAV_RATE);
}

export interface TranscribeResult { ok: boolean; status: number; text?: string; error?: string; reason?: string; install?: string; detail?: string; ms?: number }

/** POST the clip to the box. The api relays to the gateway, which relays to :5071/transcribe. */
export async function transcribeBlob(blob: Blob, fetchImpl: typeof fetch = fetch): Promise<TranscribeResult> {
  const ext = blob.type.includes('mp4') ? 'mp4' : blob.type.includes('ogg') ? 'ogg' : blob.type.includes('wav') ? 'wav' : 'webm';
  const form = new FormData();
  form.append('audio', blob, `clip.${ext}`);
  try {
    const r = await fetchImpl('/api/arturo/transcribe', { method: 'POST', body: form });
    const json = await r.json().catch(() => ({}));
    return { ok: r.ok && json.ok === true, status: r.status, ...json };
  } catch (e: unknown) {
    return { ok: false, status: 0, error: 'network', detail: (e as Error)?.message };
  }
}

/** First-person note for a failed transcription, naming the fix where there is one. */
export function transcribeReason(r: TranscribeResult): string {
  if (r.ok) return '';
  if (r.error === 'no_speech') return 'I did not catch any words — try again a little closer to the mic';
  if (r.error === 'stt_unavailable') {
    if (r.reason === 'warming') return 'the speech model (~100 MB) is still downloading on the server — try again in a minute';
    if (r.reason === 'not-installed') return `server dictation is not installed on this box — run \`${r.install || 'orchestra init'}\` (or use Chrome/Edge/Safari)`;
    if (r.reason === 'off') return 'server dictation is switched off (ARTURO_LOCAL_STT=0) — use Chrome/Edge/Safari';
    return `server dictation is unavailable${r.detail ? ` (${r.detail})` : ''}`;
  }
  if (r.error === 'wav_required') return 'this browser could not decode its own recording — use Chrome/Edge/Safari, or run `orchestra init --stt` on the box for the engine that decodes anything';
  if (r.error === 'too_large') return 'that recording is too long — keep it under a minute';
  if (r.error === 'timeout') return 'the server took too long to transcribe — try a shorter clip';
  if (r.status === 0) return 'could not reach the server to transcribe';
  return `transcription failed (${r.error || `HTTP ${r.status}`})`;
}
