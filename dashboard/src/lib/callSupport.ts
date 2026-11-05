/** callSupport.ts — whether a composer's round button can offer a call in this browser (lib/roundAction.ts). */

/** Whether this browser can hold a call at all (a socket and the microphone); why not, in words. */
export function callSupport(): { ok: boolean; reason?: string } {
  if (typeof WebSocket === 'undefined') return { ok: false, reason: "Calls aren't possible in this browser (no WebSocket)" };
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
    return { ok: false, reason: "Calls aren't possible here: this browser can't use the microphone" };
  }
  return { ok: true };
}
