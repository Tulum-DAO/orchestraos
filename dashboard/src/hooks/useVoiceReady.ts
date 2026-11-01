/** useVoiceReady — lib/voiceReady.ts: asked when the composer opens and whenever the page regains focus (no polling).
 *  null until answered, and when the server gives no answer (unknown). */
import { useEffect, useState } from 'react';
import { fetchVoiceReady, type VoiceReady } from '../lib/voiceReady';

export function useVoiceReady(): VoiceReady | null {
  const [ready, setReady] = useState<VoiceReady | null>(null);
  useEffect(() => {
    let alive = true;
    const ask = () => { void fetchVoiceReady().then((r) => { if (alive) setReady(r); }); };
    ask();
    window.addEventListener('focus', ask);
    return () => { alive = false; window.removeEventListener('focus', ask); };
  }, []);
  return ready;
}
