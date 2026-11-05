/**
 * motion.ts — Settings › Display › Motion, one switch for the app's motion: the word fade, follow-scroll, the reply's
 * rise and the avatars' gestures. Follow system = the OS's reduced-motion setting; Reduced = everything appears at
 * once; Full = motion even when the OS asks for less (CSS animations behind the OS media query still follow the OS).
 */
import { useSyncExternalStore } from 'react';
import { display, subscribeDisplay } from './deviceSettings.ts';
import type { Motion } from './displaySettings.ts';

const OS_QUERY = '(prefers-reduced-motion: reduce)';

export function reducedFor(choice: Motion, osReduced: boolean): boolean {
  return choice === 'reduced' || (choice === 'system' && osReduced);
}

const osReduced = (): boolean => typeof window !== 'undefined' && !!window.matchMedia?.(OS_QUERY).matches;

export function motionReduced(): boolean {
  return reducedFor(display().motion, osReduced());
}

function subscribeMotion(cb: () => void): () => void {
  const offDisplay = subscribeDisplay(cb);
  const mq = typeof window !== 'undefined' ? window.matchMedia?.(OS_QUERY) : null;
  mq?.addEventListener('change', cb);
  return () => { offDisplay(); mq?.removeEventListener('change', cb); };
}

export function useMotionReduced(): boolean {
  return useSyncExternalStore(subscribeMotion, motionReduced, () => false);
}
