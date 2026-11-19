/**
 * handsFreeHealth.ts — keep reading /api/arturo/health until a call is possible. The pane used to read it once, on
 * mount: a read that failed (a 502 through a proxy, Arturo still starting) left the call unavailable for as long as
 * the pane stayed up. Pure apart from the injected read and sleep, so it is tested without a server.
 */
import { handsFreeReady, isArturoDisabled, type ArturoHealth } from './arturo';

export const HEALTH_RETRY_DELAYS_MS = [1000, 2000, 3000, 5000, 5000, 10000, 10000, 15000];

export async function refreshHealthUntilReady(
  read: () => Promise<ArturoHealth>,
  onHealth: (h: ArturoHealth) => void,
  opts: { delays?: number[]; sleep?: (ms: number) => Promise<void>; cancelled?: () => boolean } = {},
): Promise<ArturoHealth | null> {
  const delays = opts.delays ?? HEALTH_RETRY_DELAYS_MS;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const cancelled = opts.cancelled ?? (() => false);
  let last: ArturoHealth | null = null;
  for (let attempt = 0; attempt <= delays.length; attempt++) {
    if (attempt > 0) await sleep(delays[attempt - 1]);
    if (cancelled()) return last;
    const h = await read();
    if (cancelled()) return last;
    last = h;
    onHealth(h);
    if (handsFreeReady(h) || isArturoDisabled(h)) return h;   // a call is possible, or Arturo is off: nothing to wait for
  }
  return last;
}

/** Why the call is dimmed. "Needs a voice key" only when the server answered and said so: while the first read is
 *  out, or when Arturo did not answer, the key is not the problem. */
export function callUnavailableReason(h: ArturoHealth | null, keyMissing: string, name: string): string {
  if (!h) return 'Checking whether a call can start…';
  if (!h.ok) return `${name} isn't answering right now; the call comes back when he does`;
  return keyMissing;
}
