/**
 * agent-status.ts — bridge to the canonical v2 state detector
 * (scripts/agent-status.py, agent-state-truth 2026-08-09).
 *
 * The web dashboard previously derived agent status from self-reported
 * state/agents/*.json blobs — a second, stale truth source. This service runs
 * the SAME detector the iOS gateway consumes (hook events + chrome-anchored
 * TUI parsing + process truth) with a stale-while-revalidate cache, so both
 * surfaces agree.
 *
 * Full-fleet scan costs ~9s; never block a request on it: serve the cached
 * snapshot immediately and refresh in the background (mirrors
 * watch_gateway.py's /agents cache).
 */
import { fileURLToPath } from 'url';
import { execFile } from 'child_process';
import { join, dirname } from 'path';
import { readdirSync, statSync } from 'fs';
import { dataDir } from '../lib/config.js';

export interface DetectorStatus {
  session: string;
  state: string;            // working|thinking|idle|stopped|waiting_permission|stranded_input|stalled|unknown
  activity: string;
  tool?: string;
  model?: string;
  context_pct?: string;
  confidence?: string;      // hook|screen|screen+hook|process
  state_age_s?: number;
  stranded?: { text: string; age_s: number };
  process?: { pid: number | null; cpu: number; rss_mb: number; uptime: string };
  [key: string]: unknown;
}

const ORCH = dataDir();

/**
 * The detector is CODE, not data. Under `orchestra up` ORCHESTRA_DIR is the data dir, so
 * joining it produced <data>/scripts/agent-status.py, every execFile ENOENTed silently and
 * the dashboard showed every seat alive:false / status:unknown / detector_age_ms:-1 forever
 * on any machine where data dir != checkout (B1 outsider finding 4). Resolution order:
 * ORCHESTRA_SCRIPTS_DIR (exported by the supervisor + orchestra-env.sh) > ORCHESTRA_ROOT >
 * the checkout this module lives in (<root>/api/dist/services/agent-status.js).
 */
export function resolveDetectorPath(env: Record<string, string | undefined>, moduleUrl: string): string {
  if (env.ORCHESTRA_SCRIPTS_DIR) return join(env.ORCHESTRA_SCRIPTS_DIR, 'agent-status.py');
  if (env.ORCHESTRA_ROOT) return join(env.ORCHESTRA_ROOT, 'scripts', 'agent-status.py');
  const here = dirname(fileURLToPath(moduleUrl));
  return join(here, '..', '..', '..', 'scripts', 'agent-status.py');
}
const DETECTOR = resolveDetectorPath(process.env, import.meta.url);
const CACHE_TTL_MS = Number(process.env.AGENT_STATUS_TTL_MS) || 15_000;    // matches the iOS gateway cache; env override is for tests
const SCAN_TIMEOUT_MS = 120_000;

// Event-driven freshness: state-event-hook.py writes a Tier-0 status event
// (UserPromptSubmit->working, Stop->idle) to state/agent-events/panes/<pane>.json
// the instant an agent starts/stops. agent-status.py reads these as push-truth,
// but this cache served the last scan for up to CACHE_TTL_MS regardless — so a
// status flip lagged behind the actual event. Busting the cache when a NEWER
// event file exists makes the very next poll trigger a fresh scan that surfaces
// the event (status flips within one poll of the message-send / agent-stop,
// instead of waiting out the 15s TTL). Cheap: one readdir of a small dir.
const EVENTS_DIR = join(ORCH, 'state', 'agent-events', 'panes');

function newestEventMtimeMs(): number {
  try {
    let newest = 0;
    for (const name of readdirSync(EVENTS_DIR)) {
      if (!name.endsWith('.json')) continue;
      const m = statSync(join(EVENTS_DIR, name)).mtimeMs;
      if (m > newest) newest = m;
    }
    return newest;
  } catch {
    return 0;  // dir absent / unreadable -> no event-bust, fall back to TTL
  }
}

// The gateway's pair (watch_gateway.py _refresh_agents_cache): eventMtime = the newest pane event
// seen BEFORE the scan started; at = when the scan FINISHED (for the TTL).
// THE LAG BUG this replaces (gm msg_190daa19 item 2): staleness was `newestEvent > at`, with `at`
// stamped at FINISH, so a Stop/UserPromptSubmit landing DURING the 3.8-8.4 s scan was "older" than
// the snapshot and ignored until the 15 s TTL. Now ANY event the scan did not see re-scans.
let cache: { at: number; eventMtime: number; data: Map<string, DetectorStatus> } =
  { at: 0, eventMtime: -1, data: new Map() };
let refreshing = false;

/** Exported for tests: is the snapshot stale at `now` given the newest event mtime? */
export function isStale(c: { at: number; eventMtime: number }, now: number, newestEvent: number): boolean {
  return (now - c.at > CACHE_TTL_MS) || newestEvent !== c.eventMtime;
}

function refresh(): Promise<void> {
  if (refreshing) return Promise.resolve();
  refreshing = true;
  const eventsBefore = newestEventMtimeMs();
  return new Promise((resolve) => {
    execFile('python3', [DETECTOR, '--all'], { timeout: SCAN_TIMEOUT_MS, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => {
        try {
          if (!err && stdout) {
            const arr = JSON.parse(stdout) as DetectorStatus | DetectorStatus[];
            const list = Array.isArray(arr) ? arr : [arr];
            const map = new Map<string, DetectorStatus>();
            for (const s of list) if (s && s.session) map.set(s.session, s);
            cache = { at: Date.now(), eventMtime: eventsBefore, data: map };
          }
        } catch { /* keep previous snapshot */ }
        refreshing = false;
        resolve();
      });
  });
}

/** Snapshot of detector states keyed by tmux session. Never blocks longer
 *  than raceMs: serves the (possibly stale) snapshot and refreshes behind. */
export async function getDetectorStates(raceMs = 250): Promise<Map<string, DetectorStatus>> {
  // event-driven bust: ANY pane event the last scan did not see (mirrors the gateway)
  const stale = isStale(cache, Date.now(), newestEventMtimeMs());
  if (stale) {
    const p = refresh();
    if (cache.data.size === 0) {
      // first call ever: give the scan a short head start, then serve whatever we have
      await Promise.race([p, new Promise((r) => setTimeout(r, raceMs))]);
    }
  }
  return cache.data;
}

export function detectorCacheAgeMs(): number {
  return cache.at ? Date.now() - cache.at : -1;
}

/**
 * Relic-vs-crashed rule for REGISTERED agents with NO tmux session — must
 * match the iOS gateway (watch_gateway.py compute_agents, GM triage
 * 2026-08-09): "crashed" (needs attention) ONLY if the agent last
 * self-reported alive; agents whose own blob says stopped/retired/dead/
 * archived are long-dead registry relics and read "offline".
 */
export const RELIC_SELF_STATES = new Set(['stopped', 'retired', 'dead', 'archived']);

/**
 * The relics that were DELIBERATELY decommissioned, as opposed to merely not
 * running. Every lineage rotation leaves one: rotating `build` to generation 2
 * writes a `build-g2` row with status `retired` and no tmux session, exactly as
 * `gm-g2` was left behind earlier.
 *
 * These used to collapse into `offline`, which reads to every client as "should
 * be running and isn't" — so the dashboard counted two decommissioned records as
 * down agents, and the org chart could pick the retired T0 as its root. Down and
 * retired are different facts and the API has to say which one it means.
 */
export const DECOMMISSIONED_SELF_STATES = new Set(['retired', 'archived']);

export function classifyNoSession(alwaysOn: boolean, selfStatus: string):
    { status: 'crashed' | 'offline' | 'retired'; activity: string } {
  // Checked before always_on: a retired seat is retired whether or not its row
  // still claims always_on — the registry row outlives the decision to run it.
  if (DECOMMISSIONED_SELF_STATES.has(selfStatus)) {
    return { status: 'retired', activity: `Decommissioned (self-reported ${selfStatus})` };
  }
  const relic = RELIC_SELF_STATES.has(selfStatus);
  if (alwaysOn && !relic) {
    return { status: 'crashed', activity: 'No tmux session' };
  }
  return {
    status: 'offline',
    activity: relic ? `Not running (self-reported ${selfStatus})` : 'Not running',
  };
}
