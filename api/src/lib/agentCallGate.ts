/**
 * Is a call to this agent the agent's own call (its session takes the turns)? The dashboard's "Call <agent>" label
 * (GET /api/agents/:id/settings `agent_calls`), by the voice side's own rule (services/arturo/agent_call.py enabled(),
 * on the relay engine), with the cases both run in contract/agent-call-gate.json:
 *  - the seat's switch is on: <data dir>/state/agent-calls.json {"seats": {"<id>": {"on": true}}}, exactly true;
 *  - the seat is headless: switched on in headless-seats.json with runtime claude, and its registry row's runtime (if
 *    it names one) is claude too (scripts/headless_switch.py is_headless); a tmux seat has no reply stream;
 *  - Dev does not change it: the call stays the agent's, and each turn asks the person to tap Back to chat (the
 *    settings' `in_dev` says so, for the call bar);
 *  - the voice engine is GPT-Live (Arturo's /health live_engine "relay"): only the relay hands turns to the seat.
 */
import fs from 'fs';
import path from 'path';
import { readGatewayToken } from './gateway-token.js';
import { gatewayUrl } from './gateway-url.js';

export type LiveEngine = 'gemini' | 'relay' | null | 'unreachable';

export interface AgentCallFacts {
  switch_on: unknown;
  headless: boolean;
  registry_runtime: string | null;
  /** In Dev: reported beside the flag, never part of it. */
  dev?: boolean;
  engine: LiveEngine;
}

/** The rule itself, over the facts (what the contract cases give). */
export function agentCallGoesToSeat(f: AgentCallFacts): boolean {
  const rt = (f.registry_runtime || '').trim().toLowerCase();
  return f.switch_on === true && f.headless && (!rt || rt === 'claude') && f.engine === 'relay';
}

/** The seat's switch value in <data dir>/state/agent-calls.json, as stored (only `true` turns it on). */
export function agentCallSwitch(dataDir: string, seat: string): unknown {
  try {
    const doc = JSON.parse(fs.readFileSync(path.join(dataDir, 'state', 'agent-calls.json'), 'utf-8'));
    const row = doc && typeof doc === 'object' && doc.seats && typeof doc.seats === 'object' ? doc.seats[seat] : null;
    return row && typeof row === 'object' ? row.on : undefined;
  } catch { return undefined; }
}

// Arturo's live engine changes only when someone changes the voice vendor or a key: read at most every 30 s, and the
// settings read never waits on it longer than a moment.
const ENGINE_TTL_MS = 30_000;
let engineCache: { at: number; value: LiveEngine } | null = null;
let inflight: Promise<LiveEngine> | null = null;

async function fetchEngine(): Promise<LiveEngine> {
  try {
    const r = await fetch(`${gatewayUrl()}/arturo/health`, {
      headers: { Authorization: `Bearer ${readGatewayToken()}` }, signal: AbortSignal.timeout(4000),
    });
    if (!r.ok) return 'unreachable';
    const e = ((await r.json()) as { live_engine?: unknown })?.live_engine;
    return e === 'gemini' || e === 'relay' ? e : e === null ? null : 'unreachable';
  } catch { return 'unreachable'; }
}

/** The live engine, cached. A cold cache waits up to `waitMs` for the answer, then reads as unreachable for this
 *  request (the fetch carries on and fills the cache for the next). */
export async function liveEngine(waitMs = 1500, now = Date.now(), fetcher: () => Promise<LiveEngine> = fetchEngine): Promise<LiveEngine> {
  if (engineCache && now - engineCache.at < ENGINE_TTL_MS) return engineCache.value;
  if (!inflight) {
    inflight = fetcher().then((value) => { engineCache = { at: Date.now(), value }; return value; })
      .finally(() => { inflight = null; });
  }
  const stale = engineCache?.value;
  return Promise.race([inflight, new Promise<LiveEngine>((r) => setTimeout(() => r(stale ?? 'unreachable'), waitMs).unref?.())]);
}

/** Tests: forget the cached engine. */
export function forgetLiveEngine(): void { engineCache = null; inflight = null; }
