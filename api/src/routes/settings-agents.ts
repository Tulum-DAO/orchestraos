/**
 * Settings › Agents, beyond the registry's rows (the settings design §4.5):
 *   GET  /api/settings/agents/overrides       -> {ok, total, agents, rows: {<setting key>: [{id, value}]}, notCounted}
 *                                                (lib/agentOverrides.ts: which agents keep their own value)
 *   GET  /api/settings/agents/processes       -> {ok, supported, canStop, processes: [{pid, startTicks, startedAt,
 *                                                program, ports, agent, via}]}  (lib/backgroundProcesses.ts)
 *        A member sees the program name and ports only; an admin also gets `command` (secrets masked) and
 *        `commandHash`. The list is read at most every 5 s (a Stop always reads it afresh, and clears the cache).
 *   POST /api/settings/agents/processes/stop  {pid, startTicks, commandHash} -> {ok, result: 'stopped' | 'killed', warning?}
 *        Admin only (403 for a member). Re-checked against the list at that moment: a pid no longer listed, or listed
 *        with another start time or command hash, is refused (409). SIGTERM, then SIGKILL after 5 s only if still
 *        alive; `warning` when something is listening on its port again. Each stop that sent a signal is written to
 *        the settings change log (state/settings-log.jsonl) with the masked command.
 */
import { Router, type Request, type Response } from 'express';
import { agentOverrides } from '../lib/agentOverrides.js';
import { listCandidates, procSource, procSupported, stopCandidate, type Candidate, type ListOptions, type ProcSource, type Seat } from '../lib/backgroundProcesses.js';
import { recordSettingChange } from '../lib/settingsLog.js';
import { installRole, changedBy, type InstallRole } from '../lib/principal.js';
import { dataDir, loadConfig } from '../lib/config.js';
import { CODE_ROOT } from '../lib/codeRoot.js';
import { gatewayUrl } from '../lib/gateway-url.js';
import { getRegistry } from '../services/state-reader.js';

type Registry = { agents?: Record<string, { runtime?: unknown; tmux_session?: unknown }> } | null;

export interface SettingsAgentsDeps {
  dataDir: () => string;
  registry: () => Registry;
  role: (req: Request) => InstallRole;
  who: (req: Request) => string;
  source: () => ProcSource;
  supported: () => boolean;
  options: () => ListOptions;
  kill: (pid: number, sig: NodeJS.Signals) => void;
  sleep: (ms: number) => Promise<void>;
  now?: () => Date;
  graceMs?: number;
  pollMs?: number;
  settleMs?: number;
  /** Milliseconds, for the list's 5 s cache. */
  clock?: () => number;
}

export const LIST_CACHE_MS = 5000;

/** What a caller may see of a candidate: a member the program's name and ports, an admin also the guessed script
 *  (detail), the masked command and its hash. */
export function processDto(c: Candidate, admin: boolean) {
  const { command, commandHash, detail, ...rest } = c;
  return admin ? { ...rest, detail, command, commandHash } : rest;
}

export const STOP_LOG_KEY = 'agents.backgroundProcesses';

export function seatsOf(registry: Registry): Seat[] {
  return Object.entries(registry?.agents ?? {}).map(([id, row]) => ({
    id, tmuxSession: row && typeof row.tmux_session === 'string' && row.tmux_session ? row.tmux_session : id,
  }));
}

export function createSettingsAgentsRouter(deps: SettingsAgentsDeps): Router {
  const router = Router();
  const now = deps.now ?? (() => new Date());
  const clock = deps.clock ?? Date.now;
  // Concurrent GETs share one read. A Stop bumps `generation`: a read that began before it is answered but not cached.
  let cache: { at: number; list: Candidate[] } | null = null;
  let generation = 0;
  let inflight: { generation: number; read: Promise<Candidate[]> } | null = null;
  const list = (): Promise<Candidate[]> => {
    if (cache && clock() - cache.at < LIST_CACHE_MS) return Promise.resolve(cache.list);
    if (inflight && inflight.generation === generation) return inflight.read;
    const g = generation;
    const read = listCandidates(deps.source(), seatsOf(deps.registry()), deps.options())
      .then((fresh) => { if (g === generation) cache = { at: clock(), list: fresh }; return fresh; })
      .finally(() => { if (inflight?.read === read) inflight = null; });
    inflight = { generation: g, read };
    return read;
  };
  const invalidate = () => { generation++; cache = null; };

  router.get('/overrides', (_req: Request, res: Response) => {
    res.json({ ok: true, ...agentOverrides(deps.dataDir(), deps.registry()) });
  });

  router.get('/processes', async (req: Request, res: Response) => {
    const canStop = deps.role(req) === 'admin';
    if (!deps.supported()) { res.json({ ok: true, supported: false, canStop, processes: [] }); return; }
    try {
      res.json({ ok: true, supported: true, canStop, processes: (await list()).map((c) => processDto(c, canStop)) });
    } catch (e) {
      res.status(500).json({ ok: false, error: `Couldn't read the running programs: ${e instanceof Error ? e.message : String(e)}` });
    }
  });

  router.post('/processes/stop', async (req: Request, res: Response) => {
    if (deps.role(req) !== 'admin') { res.status(403).json({ ok: false, error: 'Only an admin can stop a program' }); return; }
    if (!deps.supported()) { res.status(501).json({ ok: false, error: "This server can't list its programs" }); return; }
    const body = (req.body || {}) as Record<string, unknown>;
    invalidate();
    const r = await stopCandidate(deps.source(), seatsOf(deps.registry()), deps.options(),
      { pid: body.pid, startTicks: body.startTicks, commandHash: body.commandHash },
      { kill: deps.kill, sleep: deps.sleep, graceMs: deps.graceMs, pollMs: deps.pollMs, settleMs: deps.settleMs });
    invalidate();
    if (r.candidate) {
      const c = r.candidate;
      recordSettingChange(deps.dataDir(), { key: STOP_LOG_KEY, scope: 'everyone', by: deps.who(req), at: now().toISOString(),
        value: { action: 'stop', pid: c.pid, command: c.command, ports: c.ports, agent: c.agent, result: r.ok ? r.result : `failed: ${r.error}`, ...(r.ok && r.warning ? { warning: r.warning } : {}) } });
    }
    if (!r.ok) { res.status(r.status).json({ ok: false, error: r.error }); return; }
    res.json({ ok: true, result: r.result, pid: r.candidate.pid, ...(r.warning ? { warning: r.warning } : {}) });
  });

  return router;
}

/** The install's own ports, never listed (a second guard: their programs are excluded by command already). The
 *  gateway's comes from its one derivation (lib/gateway-url.ts). */
function installPorts(): number[] {
  const c = loadConfig();
  let gateway = NaN;
  try { gateway = Number(new URL(gatewayUrl()).port); } catch { /* no gateway configured */ }
  const env = [process.env.PORT, process.env.ORCHESTRA_API_PORT, process.env.ORCHESTRA_DASHBOARD_PORT];
  return [c.apiPort, c.dashboardPort, c.arturoPort, gateway, ...env.map(Number)].filter((n) => Number.isInteger(n) && n > 0);
}

export default createSettingsAgentsRouter({
  dataDir,
  registry: () => getRegistry() as Registry,
  role: (req) => installRole(req),
  who: (req) => changedBy(req),
  source: procSource,
  supported: procSupported,
  options: () => ({ codeRoot: CODE_ROOT, installPorts: installPorts() }),
  kill: (pid, sig) => process.kill(pid, sig),
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
});
