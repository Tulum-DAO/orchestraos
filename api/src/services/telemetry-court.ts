/**
 * telemetry-court.ts — the API's REUSE of B1's court-scrub disposition seam.
 *
 * The TS API does NOT re-implement the fail-closed flag check (commission HARD
 * constraint). It shells B1's Python bridge (scripts/lineage_daemon/realtime/
 * api_bridge.py), which delegates every decision to LineageFlagStore /
 * token_extractor. This is the single-source reuse — the agent-status.ts
 * precedent (execFile python3) applied to the safety seam.
 *
 * FAIL-CLOSED everywhere (BLOCKING-1 + riders):
 *   - empty/null lineage_root         => block WITHOUT calling the bridge
 *   - bridge error / non-zero / non-JSON / timeout => block (readable=false)
 *   - unreadable/absent/corrupt store => bridge returns readable=false => block
 *
 * The bridge is called at stream-OPEN and on a SLOW re-check (mid-stream flip
 * backstop); the per-delta hot path never shells out (pure TS relay).
 */
import { execFile } from 'child_process';
import { join } from 'path';

const BRIDGE_TIMEOUT_MS = 4000;

// Paths resolved at CALL time (not module load) so the env is honored per-call
// (and tests can point LINEAGE_FLAGS_PATH / ORCHESTRA_DIR at fixtures).
function paths() {
  const ORCH = process.env.ORCHESTRA_DIR || join(process.env.HOME || '', 'scripts/agent-orchestra');
  return {
    ORCH,
    BRIDGE: join(ORCH, 'scripts', 'lineage_daemon', 'realtime', 'api_bridge.py'),
    // The durable per-lineage flag denylist (written by the court detection path
    // — OUT of B1/Build-B scope). Absent today => fail-closed => everything
    // blocks (the safe INERT default, exactly as B1 specifies).
    FLAGS_PATH: process.env.LINEAGE_FLAGS_PATH || join(ORCH, 'state', 'wal', 'lineage_flags.json'),
    PYTHON: process.env.PYTHON_BIN || 'python3',
  };
}

export interface FlagStatus { flagged: boolean; readable: boolean }

/** Block <=> flagged OR !readable. A single predicate the route trusts. */
export function shouldBlock(fs: FlagStatus): boolean {
  return fs.flagged || !fs.readable;
}

/**
 * Court flag status for a lineage_root, via the Python seam. Fail-closed:
 * a null/empty root or ANY bridge failure returns { flagged:false, readable:false }
 * (=> shouldBlock === true).
 */
export function flagStatus(lineageRoot: string | null): Promise<FlagStatus> {
  // BLOCKING-1: never consult the store with an empty root — block up-front.
  if (!lineageRoot) return Promise.resolve({ flagged: false, readable: false });
  const { ORCH, BRIDGE, FLAGS_PATH, PYTHON } = paths();
  return new Promise((resolve) => {
    execFile(
      PYTHON,
      [BRIDGE, 'flag-status', '--lineage-root', lineageRoot, '--flags-path', FLAGS_PATH],
      { timeout: BRIDGE_TIMEOUT_MS, env: { ...process.env, PYTHONPATH: join(ORCH, 'scripts') } },
      (err, stdout) => {
        if (err || !stdout) return resolve({ flagged: false, readable: false });
        try {
          const o = JSON.parse(stdout);
          resolve({ flagged: !!o.flagged, readable: !!o.readable });
        } catch {
          resolve({ flagged: false, readable: false });
        }
      },
    );
  });
}
