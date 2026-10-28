// One shared EventSource on GET /api/status/stream for the whole page (lib/liveStatusOverlay.ts), opened
// while anything reads it. A dropped stream clears the readings, so the page falls back to the
// poll's instead of trusting the last thing the stream said.
import { useEffect } from 'react';
import { create } from 'zustand';
import type { LiveSeat } from '../lib/liveStatusOverlay';
import { stepPatch } from '../lib/avatarStatus';
import { avatarHub } from '../components/avatar/agentState';

interface LiveStatusState {
  seats: Record<string, LiveSeat>;
}

export const useLiveStatusStore = create<LiveStatusState>(() => ({ seats: {} }));

let source: EventSource | null = null;
let users = 0;

function open() {
  if (source || typeof EventSource === 'undefined') return;
  const es = new EventSource('/api/status/stream');
  source = es;
  const now = () => Date.now();
  es.addEventListener('snapshot', (e) => {
    try {
      const seats: Record<string, LiveSeat> = {};
      for (const s of JSON.parse((e as MessageEvent).data).seats || []) {
        if (s?.seat && typeof s.status === 'string') seats[s.seat] = { status: s.status, at: now() };
      }
      useLiveStatusStore.setState({ seats });
    } catch { /* a malformed frame: keep what we have */ }
  });
  es.addEventListener('status', (e) => {
    try {
      const d = JSON.parse((e as MessageEvent).data);
      if (!d?.seat || typeof d.status !== 'string') return;
      useLiveStatusStore.setState((st) => ({ seats: { ...st.seats, [d.seat]: { status: d.status, at: now() } } }));
    } catch { /* ignore */ }
  });
  es.addEventListener('gone', (e) => {
    try {
      const d = JSON.parse((e as MessageEvent).data);
      useLiveStatusStore.setState((st) => {
        const rest = { ...st.seats };
        delete rest[d.seat];
        return { seats: rest };
      });
    } catch { /* ignore */ }
  });
  // What a seat is doing now (Arturo's service writes it per tool step): the avatars' working line.
  es.addEventListener('step', (e) => {
    try {
      const p = stepPatch(JSON.parse((e as MessageEvent).data), now());
      if (p) avatarHub.set(p.seat, { blurb: p.blurb, blurbAt: p.blurbAt });
    } catch { /* ignore */ }
  });
  // The browser reconnects on its own (the server sends retry: 3000) and a new snapshot follows.
  es.onerror = () => useLiveStatusStore.setState({ seats: {} });
}

function close() {
  source?.close();
  source = null;
  useLiveStatusStore.setState({ seats: {} });
}

/** Live working/idle readings by seat, kept open while a component uses them. */
export function useLiveSeats(): Record<string, LiveSeat> {
  useEffect(() => {
    users += 1;
    open();
    return () => {
      users -= 1;
      if (users === 0) close();
    };
  }, []);
  return useLiveStatusStore((s) => s.seats);
}
