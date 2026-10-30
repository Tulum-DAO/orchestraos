/**
 * useHeadlessDev — the agent page's Dev for a headless seat (lib/headlessDev.ts has the rules). Asks the API whether
 * the seat is headless and whether its terminal is open (GET /api/agents/:id/dev), switches it when the chat style
 * and the seat allow (POST /api/agents/:id/dev {on}), and says what is happening. For a tmux seat it returns the
 * chosen view unchanged and does nothing else.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { create } from 'zustand';
import { sendKeyToAgent } from '../lib/api';
import {
  INITIAL, afterSwitch, bannerFor, chooseStop, chooseWait, isBusy, retry, step, styleChanged, viewOf,
  type DevBanner, type DevMemory, type SwitchResult,
} from '../lib/headlessDev';

export interface DevInfo { headless: boolean; dev: boolean }

interface DevInfoStore { info: Record<string, DevInfo | undefined>; set: (id: string, i: DevInfo) => void }

/** Per seat: is it headless, is its terminal open. Shared by the page and the agent panel's note. */
export const useDevInfoStore = create<DevInfoStore>((set) => ({
  info: {},
  set: (id, i) => set((s) => ({ info: { ...s.info, [id]: i } })),
}));

const POLL_MS = 5000;

export async function loadDevInfo(agentId: string, fetchFn: typeof fetch = fetch): Promise<DevInfo | null> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/dev`);
    const d = await r.json().catch(() => null);
    if (!r.ok || !d?.ok) return null;
    const info = { headless: d.headless === true, dev: d.dev === true };
    useDevInfoStore.getState().set(agentId, info);
    return info;
  } catch {
    return null;
  }
}

export async function postDev(agentId: string, on: boolean, fetchFn: typeof fetch = fetch): Promise<SwitchResult> {
  try {
    const r = await fetchFn(`/api/agents/${encodeURIComponent(agentId)}/dev`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ on }),
    });
    return { status: r.status, body: await r.json().catch(() => null) };
  } catch {
    return { status: 0, body: null };
  }
}

export interface HeadlessDev {
  /** What the page draws: the terminal only once the seat's TUI holds its pane. */
  view: 'dev' | 'chat';
  banner: DevBanner | null;
  act: (id: 'wait' | 'stop' | 'retry') => void;
}

export function useHeadlessDev(agentId: string, mode: 'chat' | 'dev', status: string | undefined,
  seatName: string): HeadlessDev {
  const info = useDevInfoStore((s) => s.info[agentId]);
  const [mem, setMem] = useState<DevMemory>(INITIAL);
  const wantDev = mode === 'dev';
  const busy = isBusy(status);
  const flight = useRef(false);
  const lastWant = useRef<boolean | null>(null);

  // A new seat starts over; its info is asked for at once and kept fresh (the terminal can be left from inside it).
  useEffect(() => {
    setMem(INITIAL);
    lastWant.current = null;
    void loadDevInfo(agentId);
    const t = setInterval(() => { if (!flight.current) void loadDevInfo(agentId); }, POLL_MS);
    return () => clearInterval(t);
  }, [agentId]);

  const inputs = { headless: info?.headless ?? null, actualDev: info?.dev ?? false, wantDev, busy };

  useEffect(() => {
    // The chat style changed on this page (not on its first render): a choice made here, applied before the step
    // reads the memory (one effect, so the step never sees the choice a render late).
    let base = mem;
    if (lastWant.current !== null && lastWant.current !== wantDev) base = styleChanged(mem);
    lastWant.current = wantDev;
    if (flight.current) { if (base !== mem) setMem(base); return; }
    const { mem: next, action } = step(inputs, base);
    if (JSON.stringify(next) !== JSON.stringify(mem)) setMem(next);
    if (action === 'none') return;
    const on = action === 'switchOn';
    flight.current = true;
    void postDev(agentId, on).then(async (r) => {
      const out = afterSwitch(next, on, r);
      if (out.headless === false) useDevInfoStore.getState().set(agentId, { headless: false, dev: false });
      else await loadDevInfo(agentId);
      flight.current = false;
      setMem(out.mem);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, inputs.headless, inputs.actualDev, wantDev, busy, mem.phase]);

  const act = useCallback((id: 'wait' | 'stop' | 'retry') => {
    if (id === 'wait') setMem((m) => chooseWait(m));
    else if (id === 'retry') { setMem((m) => retry(m)); void loadDevInfo(agentId); }
    else {
      setMem((m) => chooseStop(m));
      // The CLI's own interrupt (the API routes a headless seat's Escape there); the switch follows once it is idle.
      void sendKeyToAgent(agentId, 'escape');
    }
  }, [agentId]);

  return { view: viewOf(inputs), banner: info?.headless ? bannerFor(mem, seatName) : null, act };
}
