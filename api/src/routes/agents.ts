import { Router, type Request, type Response } from 'express';
import { writeFileSync, mkdirSync, existsSync, appendFileSync, readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { execFileSync } from 'child_process';
import { getRegistry, getAllAgentStates, getInboxCounts } from '../services/state-reader.js';
import { getTmuxSessionNames } from '../services/tmux-monitor.js';
import { getUnifiedAgentStatus, spawnAgent, killAgent, getMacStatus, getMacSessionsCache } from '../services/cross-machine.js';
import { logInteraction } from '../services/learning.js';
import { isTranscriptActive } from '../services/transcript-activity.js';
import { getDetectorStates, detectorCacheAgeMs, classifyNoSession, type DetectorStatus } from '../services/agent-status.js';
import { isCutoverActive, getCanonicalAgents, canonicalTmuxSession } from '../services/identity-store-reader.js';
import { buildCanonicalIndex, supersededBy, type CanonicalIndex } from '../services/rotation-leftovers.js';
import { applyIdentityPrecedence, resolveMachineAndLiveness, discoverUnregistered } from './agents-identity.js';
import { loadConfig } from '../lib/config.js';
import { readGatewayToken } from '../lib/gateway-token.js';
import { resolveSpecialKey } from '../lib/special-keys.js';
import { actingAgent, principal } from '../lib/principal.js';
import { agentScopeParam, canPrincipalSeeAgent } from '../lib/agent-scope.js';

function macSshTarget(): string {
  const cfg = loadConfig();
  return `${cfg.macSshUser}@${cfg.macTailscaleIp}`;
}

// Merge canonical v2 detector truth (agent-state-truth 2026-08-09) into an
// agent row. The self-reported state blob is kept as self_reported_status;
// `status` becomes the detector state so the web dashboard and the iOS app
// read the SAME truth source.
function mergeDetector(agent: AgentEntry, d: DetectorStatus | undefined): void {
  if (!d) return;
  agent.self_reported_status = agent.status;
  agent.status = d.state;
  agent.status_source = 'detector';
  agent.activity = d.activity || '';
  // Corroboration, not override: the detector wins whenever it sees work. It only gets
  // second-guessed in the one direction it was provably wrong on 2026-09-29 — reporting
  // idle for every seat at once, including seats mid-turn. A transcript being appended to
  // right now is direct evidence of an open turn, so trust that over an idle verdict.
  // status_source records which signal actually decided, so a wrong answer is traceable
  // to its source rather than blamed on "the dashboard".
  if (d.state === 'idle' && isTranscriptActive(agent.id)) {
    agent.status = 'working';
    agent.status_source = 'transcript';
    agent.activity = 'Working (transcript active; detector reported idle)';
  }
  agent.tool = d.tool || '';
  agent.context_pct = d.context_pct || '';
  agent.confidence = d.confidence;
  agent.state_age_s = d.state_age_s;
  if (d.stranded) agent.stranded = d.stranded;
  // pending_menu (spec 2026-08-10-interleaving-and-decision-options §2): the ONE
  // detector truth for "a real decision is waiting" — replaces the client-side
  // pane-scrape (agentActivity.ts trailing-? heuristic, the operator's stale-blue class).
  // Additive + nullable; list rows also get a boolean has_pending_menu for chips/
  // badges (full menu object still rides for the detail fetch / OptionsCard).
  agent.pending_menu = (d.pending_menu as any) ?? null;
  agent.has_pending_menu = !!d.pending_menu;
  agent.cpu = (d.process as any)?.cpu;
  agent.tmux_alive = true;
  agent.alive = true;
}

const router = Router();
// Every /:id route on this router is scoped to the caller's principal — the same rule GET /
// applies (lib/agent-scope.ts). Registered as a param handler so a /:id route added later is
// scoped without anyone remembering to; an out-of-scope id gets the same 404 as an unknown one.
router.param('id', agentScopeParam);

interface AgentEntry {
  id: string;
  tier: string;
  name: string;
  machine: string;
  tmux_session: string;
  always_on: boolean;
  status: string;
  current_task: string | null;
  last_updated: string | null;
  tmux_alive: boolean;
  inbox_count: number;
  machine_status: string;
  /** Set ONLY on a parked rotation leftover (DEC-1790826247484632): the canonical root that
   *  supersedes this row. Absent on every other row, so a client that does not know the field
   *  behaves exactly as before. */
  superseded_by?: string;
  [key: string]: unknown;
}

// Load plain English agent descriptions for client-facing views
function getAgentDescriptions(): Record<string, string> {
  const descFile = join(process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra'), 'state', 'agent-descriptions.json');
  try { return JSON.parse(readFileSync(descFile, 'utf-8')); } catch { return {}; }
}

router.get('/', async (_req: Request, res: Response) => {
  try {
    const registry = getRegistry() as Record<string, unknown> | null;
    const agentDefs = (registry?.agents ?? {}) as Record<string, Record<string, unknown>>;
    // DB-first under cutover (DEC-1788554471): registry.json flaps on foreign-branch
    // checkouts of the shared tree, dropping brand-new agents -> phantom
    // `unregistered:<name>` chips. Union the DB canonical live head over the flat
    // agentDefs (DB-wins per root; NEVER drops a flat key), so a registered live
    // agent is in the registered set (no unregistered mint) and carries the CURRENT
    // live-head tmux_session. Fail-safe: if the DB is absent/unreadable the flat
    // registry is used unchanged. INERT: the DB is touched only when armed.
    // DEC-1790826247484632: the same canonical read also answers "is this row a parked
    // rotation leftover". Kept null outside cutover / on an unreadable DB, which means the
    // leftover rule does not fire at all rather than degrade to name-plus-status.
    let canonIndex: CanonicalIndex | null = null;
    if (isCutoverActive()) {
      try {
        const canon = getCanonicalAgents();
        canonIndex = buildCanonicalIndex(canon);
        if (canon) {
          for (const [root, c] of Object.entries(canon)) {
            const existing = agentDefs[root] || {};
            agentDefs[root] = {
              name: root,
              tier: c.tier ?? undefined,
              machine: c.machine ?? undefined,
              runtime: c.runtime ?? undefined,
              cwd: c.cwd ?? undefined,
              always_on: true,
              lineage_root: root,
              ...existing,            // keep registry's richer fields when present
              tmux_session: c.tmux_session,   // DB wins on the live-head identity
              generation: c.generation ?? (existing as any).generation,
            } as Record<string, unknown>;
          }
        }
      } catch { /* fail-safe: flat registry unchanged */ }
    }
    const states = getAllAgentStates();
    const inboxCounts = getInboxCounts();

    // Fast path: get local VPS tmux sessions (instant, no SSH)
    const localSessions = getTmuxSessionNames();

    // Start unified status in background (may include Mac SSH probe)
    // Don't await — use cached data for immediate response
    const unifiedStatusPromise = getUnifiedAgentStatus(registry);
    // Give it 200ms to complete, otherwise use local-only data
    let unifiedStatus: Record<string, any> = {};
    try {
      unifiedStatus = await Promise.race([
        unifiedStatusPromise,
        new Promise<Record<string, any>>((resolve) => setTimeout(() => resolve({}), 200))
      ]);
    } catch { /* use empty */ }

    const agents: AgentEntry[] = [];
    for (const [id, def] of Object.entries(agentDefs)) {
      const state = states[id] || {};
      const tmuxSession = (def.tmux_session as string) || id;
      const unified = unifiedStatus[id];
      // If unified status not ready, check local tmux directly for VPS agents
      // Liveness-pre-union fix (gm msg_7d936165): observed local liveness beats
      // the machine label — a DB-union seat with NULL machine must not read
      // alive:false while its tmux session is live here (agents-identity.ts).
      const { machine, alive: isLocalAlive } =
        resolveMachineAndLiveness(def.machine as string | undefined, tmuxSession, localSessions as Set<string>);
      const agent: AgentEntry = {
        id,
        tier: (def.tier as string) || 'T2',
        name: (def.name as string) || id,
        machine,
        tmux_session: tmuxSession,
        always_on: (def.always_on as boolean) || false,
        status: (state.status as string) || 'unknown',
        current_task: (state.current_task as string) || null,
        last_updated: (state.last_updated as string) || null,
        tmux_alive: unified?.tmux_alive ?? isLocalAlive,
        alive: unified?.tmux_alive ?? isLocalAlive,
        inbox_count: inboxCounts[id] || 0,
        machine_status: unified?.machine_status ?? (machine === 'vps' ? 'online' : 'unknown'),
        // Spread remaining def and state fields
        ...def,
        ...state,
      };
      // R3 precedence law: identity fields flow DOWN from DB/registry only —
      // the state-file spread above must not clobber them (blank-name spawn
      // stubs blanked seats off the web dashboard; see agents-identity.ts).
      applyIdentityPrecedence(agent, def, id, tmuxSession, machine, unified?.tmux_alive ?? isLocalAlive);
      // DEC-1790826247484632: flag a parked rotation leftover so the fleet view can undraw it.
      // AFTER applyIdentityPrecedence, which owns `status`/`retired` — a leftover is NOT
      // retired (the registry never said so) and `status` stays `parked`, so the evidence a
      // reader needs to audit this survives in the payload. `def.status`, not `agent.status`:
      // the state spread and the detector pass both overwrite the latter.
      const superseded = supersededBy({
        id,
        defStatus: def.status as string | undefined,
        alive: agent.alive as boolean,
        index: canonIndex,
      });
      if (superseded) (agent as any).superseded_by = superseded;
      // Defense-in-depth: the /agents UI iterates several fields with
      // (field||[]).forEach / .includes / .map — that guards null but NOT a
      // wrong TYPE. A registry record with a string where an array is expected
      // (e.g. tags:"a,b,c" or memory_scope:"global") white-screens the WHOLE
      // page. Coerce every array-expected field at the API boundary so one
      // malformed record can never crash the client. (Comma-string → split;
      // any other non-array/non-string → []).
      for (const f of ['tags', 'memory_scope', 'channels', 'blockers', 'files_touched', 'can_spawn', 'capabilities', 'skills']) {
        const v = (agent as any)[f];
        if (v === undefined) continue;
        if (typeof v === 'string') {
          (agent as any)[f] = v.split(',').map((s: string) => s.trim()).filter(Boolean);
        } else if (!Array.isArray(v)) {
          (agent as any)[f] = [];
        }
      }
      agents.push(agent);
    }

    // Discover unregistered tmux sessions (VPS local + Mac via cached probe)
    const registeredSessions = new Set(agents.map(a => a.tmux_session));
    const allTmuxSessions = localSessions;
    const unregistered: AgentEntry[] = [];

    // Registry-scoped by default (gm ruling msg_9f04c5f0): tmux is host-global.
    const showUnregistered = loadConfig().showUnregisteredSessions;
    unregistered.push(...discoverUnregistered(allTmuxSessions as Set<string>, registeredSessions, showUnregistered));

    // Discover unregistered sessions from ALL machines via heartbeats (same gate)
    if (showUnregistered) try {
      const { getMachineSessionsFromHeartbeat, getMachineStatus: getHBMachineStatus } = await import('./machines.js');
      const machinesToScan = ['mac', 'kai-studio', 'kai-macbook'];
      for (const machineId of machinesToScan) {
        const sessions = getMachineSessionsFromHeartbeat(machineId);
        const machineStatus = getHBMachineStatus(machineId);
        for (const session of sessions) {
          if (!registeredSessions.has(session) && !session.startsWith('session-')) {
            if (allTmuxSessions.has(session)) continue; // skip if on VPS
            unregistered.push({
              id: `${machineId}:${session}`,
              tier: 'T2',
              name: session,
              machine: machineId,
              tmux_session: session,
              always_on: false,
              status: 'running',
              current_task: null,
              last_updated: null,
              tmux_alive: true,
              alive: true,
              inbox_count: 0,
              machine_status: machineStatus === 'no_heartbeat' ? 'unknown' : machineStatus,
              unregistered: true,
            });
          }
        }
      }
    } catch {
      // Fallback: use old Mac cache probe if heartbeat module unavailable
      try {
        const macSessions = getMacSessionsCache();
        const macState = getMacStatus();
        for (const session of macSessions) {
          if (!registeredSessions.has(session) && !session.startsWith('session-')) {
            if (allTmuxSessions.has(session)) continue;
            unregistered.push({
              id: `mac:${session}`,
              tier: 'T2',
              name: session,
              machine: 'mac',
              tmux_session: session,
              always_on: false,
              status: 'running',
              current_task: null,
              last_updated: null,
              tmux_alive: true,
              alive: true,
              inbox_count: 0,
              machine_status: macState.status,
              unregistered: true,
            });
          }
        }
      } catch {}
    }

    // Non-tmux Claude instances — skip on VPS (slow lsof, minimal value)
    const nonTmuxInstances: { tty: string; pid: string; cwd: string }[] = [];

    // Combine all
    const allAgentsRaw = [...agents, ...unregistered];

    // Boundary de-dup (gm ruling msg_3c64434e, 2026-09-02): a retired/archived
    // predecessor row can carry the SAME id as its live successor (its stored def
    // spreads its own `id` field over the constructed one, e.g. the
    // rotation-autonomy-builder-gen2 archive row emitting id=rotation-autonomy-builder),
    // and a same-id dead row clobbers chips on last-writer-wins clients — including
    // the FROZEN iOS/watch surface which cannot be patched client-side. Contract:
    // exactly ONE row per id. Deterministic preference: alive first; then
    // non-retired status; then highest generation; then most recent
    // last_active/last_updated. Ties keep the incumbent. Never hides a live row
    // (a live row always outranks a dead one on the first key).
    const DEDUP_RETIRED = new Set(['offline', 'retired', 'dead', 'archived', 'stopped']);
    const dedupScore = (r: AgentEntry): [number, number, number, number] => [
      r.alive === true ? 1 : 0,
      DEDUP_RETIRED.has(String((r as any).status || '').toLowerCase()) ? 0 : 1,
      typeof (r as any).generation === 'number' ? (r as any).generation : -1,
      Math.max(
        Date.parse(String((r as any).last_active || '')) || 0,
        Date.parse(String((r as any).last_updated || '')) || 0,
      ),
    ];
    const dedupById = new Map<string, AgentEntry>();
    for (const row of allAgentsRaw) {
      const cur = dedupById.get(row.id);
      if (!cur) { dedupById.set(row.id, row); continue; }
      const a = dedupScore(cur), b = dedupScore(row);
      for (let i = 0; i < a.length; i++) {
        if (b[i] > a[i]) { dedupById.set(row.id, row); break; }
        if (b[i] < a[i]) break; // incumbent wins; full tie also keeps incumbent
      }
    }
    const allAgents = [...dedupById.values()];

    // Unify on the canonical v2 detector (VPS sessions). Served from a 15s
    // stale-while-revalidate cache — never blocks the request on the ~9s scan.
    try {
      const detector = await getDetectorStates();
      // Cold start (no scan yet): apply NOTHING — legacy fields stand, and the
      // no-session reclassification below must not fire off an empty snapshot.
      if (detector.size > 0) {
        for (const agent of allAgents) {
          // The machine label is a label, not evidence (liveness-pre-union rule): a session
          // that is live on THIS host gets the detector's verdict whatever its row says —
          // on a single-machine install rows may carry machine=mac (B1 finding 4 follow-up).
          if (agent.machine !== 'vps' && !localSessions.has(agent.tmux_session)) continue;
          const d = detector.get(agent.tmux_session);
          if (d) { mergeDetector(agent, d); continue; }
          if (agent.unregistered) continue;
          const selfStatus = String(agent.status || '');
          agent.self_reported_status = selfStatus;
          agent.status_source = 'detector';
          if (!localSessions.has(agent.tmux_session)) {
            // Registered agent with NO tmux session — same rule as the iOS
            // gateway (one truth RULE, not just one truth source).
            const cls = classifyNoSession(!!agent.always_on, selfStatus);
            agent.status = cls.status;
            agent.activity = cls.activity;
            agent.tmux_alive = false;
            agent.alive = false;
          } else {
            // Session exists but holds no claude process (service pane or
            // shell) — same semantics as the detector/gateway: stopped.
            agent.status = 'stopped';
            agent.activity = 'No claude process running';
          }
        }
      }
    } catch { /* detector unavailable -> legacy fields stand */ }

    allAgents.sort((a, b) => a.tier.localeCompare(b.tier) || a.name.localeCompare(b.name));

    const online = allAgents.filter((a) => a.tmux_alive).length;

    // Add plain English descriptions for client-facing views
    const descriptions = getAgentDescriptions();
    for (const agent of allAgents) {
      const baseId = agent.id.replace('unregistered:', '').replace('mac:', '');
      if (descriptions[baseId]) {
        agent.client_description = descriptions[baseId];
      }
    }

    // Server-side agent filtering by user permissions. Scope comes from the principal,
    // not raw headers: untrusted mode yields '*' (today's behaviour, unchanged), trusted
    // mode yields [] when the proxy asserted no scope — fail closed, not wide open.
    // The rule lives in lib/agent-scope.ts and is shared with every /:id route, so the list and
    // the per-agent routes cannot drift apart: before 2026-10-02 this was the ONLY place it was
    // applied, and the 16 /:id routes beside it had no check at all.
    const _p = principal(_req);
    const visibleAgents = allAgents.filter((a) => canPrincipalSeeAgent(_p, a));

    const visibleOnline = visibleAgents.filter((a) => a.tmux_alive).length;

    res.json({
      agents: visibleAgents,
      total: visibleAgents.length,
      online: visibleOnline,
      unregistered_tmux: unregistered.length,
      non_tmux_claude: nonTmuxInstances,
      non_tmux_count: nonTmuxInstances.length,
      detector_age_ms: detectorCacheAgeMs(),
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load agents', detail: String(err) });
  }
});

router.get('/:id', async (req: Request, res: Response) => {
  try {
    const id = req.params.id as string;
    const registry = getRegistry() as Record<string, unknown> | null;
    const agentDefs = (registry?.agents ?? {}) as Record<string, Record<string, unknown>>;
    const def = agentDefs[id];
    if (!def) {
      res.status(404).json({ error: `Agent '${id}' not found` });
      return;
    }

    const states = getAllAgentStates();
    const state = states[id] || {};
    const inboxCounts = getInboxCounts();
    const tmuxSession = (def.tmux_session as string) || id;

    const agent = {
      id,
      ...def,
      ...state,
      tmux_alive: getTmuxSessionNames().has(tmuxSession),
      inbox_count: inboxCounts[id] || 0,
    } as unknown as AgentEntry;

    // Same canonical detector truth as the fleet list (cached; non-blocking).
    try {
      if (((def.machine as string) || 'vps') === 'vps') {
        const detector = await getDetectorStates();
        mergeDetector(agent, detector.get(tmuxSession));
      }
    } catch { /* legacy fields stand */ }

    res.json(agent);
  } catch (err) {
    res.status(500).json({ error: 'Failed to load agent', detail: String(err) });
  }
});

// POST /api/agents/:id/spawn
router.post('/:id/spawn', async (req: Request, res: Response) => {
  try {
    const registry = getRegistry();
    const task = req.body?.task;
    const result = await spawnAgent(req.params.id as string, registry, task);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: 'Spawn failed', detail: String(err) });
  }
});

// POST /api/agents/:id/kill
router.post('/:id/kill', async (req: Request, res: Response) => {
  try {
    const registry = getRegistry();
    const result = await killAgent(req.params.id as string, registry);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: 'Kill failed', detail: String(err) });
  }
});

/**
 * POST /api/agents/:id/message — GONE (410) since 2026-09-30.
 *
 * This wrote a JSON file into `queue/inbox/<agent>/` and answered `{sent:true}`. Nothing
 * reads that directory — prompts/infrastructure.md says so outright ("DO NOT use
 * queue/inbox/ — they are deprecated") — so every message sent through it was silently
 * discarded while the UI reported success. Found by test during the 2D QA pass and
 * reproduced twice, once through the UI and once by curl straight at this route. The
 * operator could believe an instruction had reached an agent and have it vanish.
 *
 * It answers 410 rather than being deleted outright, deliberately. A deleted route 404s,
 * which reads as a typo or a stale client; 410 with a pointer tells any surviving caller
 * exactly what happened and what to use instead. The one thing this path must never do
 * again is succeed.
 *
 * Durable replacement: POST /api/agents/:id/send (api/src/routes/agent-send.ts), which goes
 * through msg_store. Client: sendToAgent() in dashboard/src/lib/agentSend.ts.
 */
export function handleDeprecatedMessage(req: Request, res: Response): void {
  console.warn('[gone] POST /api/agents/:id/message — a caller is still using the dropped send path', {
    agentId: req.params.id,
    hasBody: Boolean(req.body?.message),
  });
  res.status(410).json({
    error: 'This endpoint is gone: it wrote to queue/inbox/, which no agent reads.',
    code: 'endpoint_gone',
    use_instead: 'POST /api/agents/:id/send',
  });
}
router.post('/:id/message', handleDeprecatedMessage);

// GET /api/agents/:id/output — capture last N lines from agent's tmux pane
router.get('/:id/output', (req: Request, res: Response) => {
  const registry = getRegistry() as any;
  const agent = registry?.agents?.[String(req.params.id)];

  // Support both registered and unregistered agents
  let session: string;
  if (agent) {
    session = (agent.tmux_session as string) || String(req.params.id);
  } else if (String(req.params.id).startsWith('unregistered:')) {
    session = String(req.params.id).replace('unregistered:', '');
  } else {
    session = String(req.params.id);
  }
  // DB-first click target under cutover (DEC-1788554471): if the id is a canonical
  // root, attach to the CURRENT live head from the DB — never a stale registry
  // tmux_session that would exit/miss the pane. Fail-safe: keep `session` on any miss.
  if (isCutoverActive()) {
    try {
      const live = canonicalTmuxSession(String(req.params.id));
      if (live) session = live;
    } catch { /* fail-safe: keep the flat-resolved session */ }
  }

  const lines = parseInt(req.query.lines as string) || 50;
  const machine = agent?.machine || 'vps';

  try {
    let output: string;
    if (machine === 'mac') {
      // Mac agent — SSH to Mac
      output = execFileSync('ssh', [
        '-o', 'ConnectTimeout=3', '-o', 'StrictHostKeyChecking=no',
        '-o', 'IdentitiesOnly=yes', '-i', `${process.env.HOME}/.ssh/id_ed25519`,
        macSshTarget(),
        `/opt/homebrew/bin/tmux capture-pane -t ${session} -p -S -${lines}`,
      ], { encoding: 'utf-8', timeout: 8000 });
    } else {
      // VPS agent — local tmux (no SSH needed)
      output = execFileSync('tmux', ['capture-pane', '-t', session, '-p', '-S', `-${lines}`], {
        encoding: 'utf-8', timeout: 5000
      });
    }
    res.json({ agent_id: req.params.id, session, lines: output.trim().split('\n'), raw: output.trim() });
  } catch (err: any) {
    res.json({ agent_id: req.params.id, session, lines: [], raw: '', error: 'Session not found or not attached' });
  }
});

// Helper: capture tmux pane content
function captureTmux(session: string, machine: string, lines: number = 50): string {
  try {
    if (machine === 'mac') {
      return execFileSync('ssh', [
        '-o', 'ConnectTimeout=3', '-o', 'StrictHostKeyChecking=no',
        '-o', 'IdentitiesOnly=yes', '-i', `${process.env.HOME}/.ssh/id_ed25519`,
        macSshTarget(),
        `/opt/homebrew/bin/tmux capture-pane -t ${session} -p -S -${lines}`,
      ], { encoding: 'utf-8', timeout: 8000 }).trim();
    }
    return execFileSync('tmux', ['capture-pane', '-t', session, '-p', '-S', `-${lines}`], {
      encoding: 'utf-8', timeout: 5000
    }).trim();
  } catch { return ''; }
}

// Helper: verified injection via the watch gateway (the ONE 3-gated transport:
// idle-state gate, active-turn guard, composer-occupied guard w/ ghost
// discrimination + force override). Web and iOS now share this path — raw
// send-keys remains ONLY for Mac agents the local gateway can't reach.
// #85 reader: WATCH_GATEWAY_TOKEN_FILE (what `orchestra init` exports) first, the legacy
// ~/.config/jarvis path only as a fallback. A fresh install has no ~/.config/jarvis at all.
const GATEWAY_URL = process.env.WATCH_GATEWAY_URL || 'http://127.0.0.1:9091';

async function gatewayInject(session: string, text: string, force: boolean):
    Promise<{ delivered: boolean; busy?: boolean; reason?: string; state?: string; activity?: string }> {
  const token = readGatewayToken();
  if (!token) throw new Error('gateway token unavailable');
  const resp = await fetch(`${GATEWAY_URL}/agent-message`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ session, text, force }),
    signal: AbortSignal.timeout(25000),
  });
  const body: any = await resp.json().catch(() => ({}));
  return {
    delivered: !!body.delivered,
    busy: !!body.busy,
    reason: body.reason,
    state: body.state,
    activity: body.activity,
  };
}

// Helper: answer a pending decision menu via the gateway's two-phase /agent-key
// (spec 2026-08-10-interleaving-and-decision-options §2). Same 3-gated transport
// family as gatewayInject — the gateway re-checks the LIVE detector for a
// pending_menu (the menu IS the proof a digit is safe), so a menu that vanished
// between web render and tap returns 409 (answered elsewhere). Digits 1-9 only;
// confirm:false -> 428 needs_confirm; confirm:true -> sends the digit (no Enter).
// Returns the gateway status verbatim so the OptionsCard can drive its UX.
async function gatewayKey(session: string, key: string, confirm: boolean):
    Promise<{ status: number; body: any }> {
  const token = readGatewayToken();
  if (!token) throw new Error('gateway token unavailable');
  const resp = await fetch(`${GATEWAY_URL}/agent-key`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ session, key, confirm }),
    signal: AbortSignal.timeout(10000),
  });
  const body: any = await resp.json().catch(() => ({}));
  return { status: resp.status, body };
}

// Helper: send keys to tmux session (LEGACY — Mac agents only; VPS goes
// through gatewayInject above)
function sendToTmux(session: string, machine: string, text: string) {
  if (machine === 'mac') {
    const escaped = text.replace(/'/g, "'\\''");
    execFileSync('ssh', [
      '-o', 'ConnectTimeout=3', '-o', 'StrictHostKeyChecking=no',
      '-o', 'IdentitiesOnly=yes', '-i', `${process.env.HOME}/.ssh/id_ed25519`,
      macSshTarget(),
      `/opt/homebrew/bin/tmux send-keys -t ${session} '${escaped}' Enter`,
    ], { timeout: 8000 });
  } else {
    execFileSync('tmux', ['send-keys', '-t', session, text, 'Enter'], { timeout: 5000 });
  }
}

// POST /api/agents/:id/inject — send text into running tmux session, poll for output
router.post('/:id/inject', async (req: Request, res: Response) => {
  const { text } = req.body;
  if (!text) { res.status(400).json({ error: 'text required' }); return; }

  const registry = getRegistry() as any;
  const agent = registry?.agents?.[String(req.params.id)];

  let session: string;
  if (agent) {
    session = (agent.tmux_session as string) || String(req.params.id);
  } else if (String(req.params.id).startsWith('unregistered:')) {
    session = String(req.params.id).replace('unregistered:', '');
  } else {
    res.status(404).json({ error: 'Agent not found' }); return;
  }

  const machine = agent?.machine || 'vps';
  const isLongText = text.length > 120 || text.includes('\n');

  try {
    // Capture "before" snapshot
    const beforeOutput = captureTmux(session, machine, 30);

    if (machine !== 'mac') {
      // VPS: the ONE verified transport (same as iOS). It gates on agent
      // state, never pastes over typed input, and verifies the submit —
      // the raw-send "extra Enter" hack is obsolete on this path.
      const r = await gatewayInject(session, text, !!req.body.force);
      if (!r.delivered) {
        res.status(r.busy ? 409 : 502).json({
          injected: false, busy: !!r.busy, reason: r.reason,
          state: r.state, activity: r.activity,
        });
        return;
      }
    } else {
      // Mac agents: legacy raw path (local gateway can't reach Mac tmux).
      sendToTmux(session, machine, text);
      if (isLongText) {
        await new Promise(r => setTimeout(r, 500));
        try {
          execFileSync('ssh', [
            '-o', 'ConnectTimeout=3', '-o', 'StrictHostKeyChecking=no',
            '-o', 'IdentitiesOnly=yes', '-i', `${process.env.HOME}/.ssh/id_ed25519`,
            macSshTarget(),
            `/opt/homebrew/bin/tmux send-keys -t ${session} Enter`,
          ], { timeout: 8000 });
        } catch {}
      }
    }

    // Poll for new output (up to 8 seconds, every 500ms)
    let newOutput = '';
    for (let i = 0; i < 16; i++) {
      await new Promise(r => setTimeout(r, 500));
      const currentOutput = captureTmux(session, machine, 30);
      if (currentOutput && currentOutput !== beforeOutput) {
        newOutput = currentOutput;
        // Check if agent is still processing (look for spinner/working indicators)
        const lastLine = currentOutput.split('\n').filter(l => l.trim()).pop() || '';
        const isStillWorking = /[⏳⏵✻●◐◑◒◓⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/.test(lastLine) ||
          /\b(working|thinking|processing|reading|writing|running)\b/i.test(lastLine);
        if (!isStillWorking) break;
      }
    }

    // Log activity
    const activityFile = join(process.env.ORCHESTRA_DIR!, 'activity.jsonl');
    const event = JSON.stringify({
      timestamp: new Date().toISOString(),
      agent: req.params.id,
      event: 'message_injected',
      detail: text.slice(0, 100)
    });
    try { appendFileSync(activityFile, event + '\n'); } catch {}

    // LEARNING: Log dashboard inject interaction
    const user = actingAgent(req) || loadConfig().operatorId;
    logInteraction({
      channel: 'dashboard',
      userId: user,
      input: text.slice(0, 500),
      tool: 'inject_message',
      toolArgs: { agent_id: req.params.id, session },
      toolResult: 'success',
      response: newOutput ? newOutput.slice(0, 500) : '',
      latencyMs: Date.now() - Date.now(), // TODO: track actual timing
    });

    res.json({
      injected: true,
      agent_id: req.params.id,
      session,
      output: newOutput ? newOutput.split('\n') : [],
      raw_output: newOutput,
    });
  } catch (err: any) {
    // LEARNING: Log failure
    const user = actingAgent(req) || loadConfig().operatorId;
    logInteraction({
      channel: 'dashboard',
      userId: user,
      input: (req.body?.text || '').slice(0, 500),
      tool: 'inject_message',
      toolArgs: { agent_id: req.params.id },
      toolResult: 'error',
      error: err.message,
    });
    res.status(500).json({ error: 'Failed to inject', detail: err.message });
  }
});

// POST /api/agents/:id/key — send special keys (Escape, Enter, numbers, Ctrl-C, etc.)
router.post('/:id/key', (req: Request, res: Response) => {
  const { key } = req.body;
  if (!key) { res.status(400).json({ error: 'key required' }); return; }

  const registry = getRegistry() as any;
  const agent = registry?.agents?.[String(req.params.id)];
  let session: string;
  if (agent) {
    session = (agent.tmux_session as string) || String(req.params.id);
  } else if (String(req.params.id).startsWith('unregistered:')) {
    session = String(req.params.id).replace('unregistered:', '');
  } else {
    res.status(404).json({ error: 'Agent not found' }); return;
  }

  // Key policy lives in lib/special-keys.ts (ctrl-z refused: RED ALERT)
  const resolved = resolveSpecialKey(String(key));
  if (!resolved.ok) { res.status(400).json({ error: resolved.reason, key }); return; }
  const tmuxKeys = resolved.tmux;
  const machine = agent?.machine || 'vps';

  try {
    for (const k of tmuxKeys) {
      if (machine === 'mac') {
        execFileSync('ssh', [
          '-o', 'ConnectTimeout=3', '-o', 'StrictHostKeyChecking=no',
          '-o', 'IdentitiesOnly=yes', '-i', `${process.env.HOME}/.ssh/id_ed25519`,
          macSshTarget(),
          `/opt/homebrew/bin/tmux send-keys -t ${session} ${k}`,
        ], { timeout: 8000 });
      } else {
        execFileSync('tmux', ['send-keys', '-t', session, k], { timeout: 5000 });
      }
    }
    res.json({ sent: true, key, session, machine, agent_id: req.params.id });
  } catch (err: any) {
    res.status(500).json({ error: 'Failed to send key', detail: err.message, machine });
  }
});

// POST /api/agents/:id/inject-raw — send text WITHOUT auto-Enter (for interactive prompts)
router.post('/:id/inject-raw', (req: Request, res: Response) => {
  const { text } = req.body;
  if (!text) { res.status(400).json({ error: 'text required' }); return; }

  const registry = getRegistry() as any;
  const agent = registry?.agents?.[String(req.params.id)];
  let session: string;
  if (agent) {
    session = (agent.tmux_session as string) || String(req.params.id);
  } else if (String(req.params.id).startsWith('unregistered:')) {
    session = String(req.params.id).replace('unregistered:', '');
  } else {
    res.status(404).json({ error: 'Agent not found' }); return;
  }

  try {
    // Send text without Enter — for typing into prompts
    execFileSync('tmux', ['send-keys', '-t', session, text], { timeout: 5000 });
    res.json({ injected: true, agent_id: req.params.id, session, noEnter: true });
  } catch (err: any) {
    res.status(500).json({ error: 'Failed to inject', detail: err.message });
  }
});

// POST /api/agents/:id/agent-key — answer a pending decision menu (OptionsCard).
// Proxies the gateway's two-phase /agent-key; passes the status through so the
// web card mirrors iOS (428 needs_confirm, 409 menu gone, 403 key not enabled).
router.post('/:id/agent-key', async (req: Request, res: Response) => {
  const { key, confirm } = req.body || {};
  if (!key) { res.status(400).json({ error: 'key required' }); return; }

  const registry = getRegistry() as any;
  const agent = registry?.agents?.[String(req.params.id)];
  let session: string;
  let machine = 'vps';
  if (agent) {
    session = (agent.tmux_session as string) || String(req.params.id);
    machine = (agent.machine as string) || 'vps';
  } else if (String(req.params.id).startsWith('unregistered:')) {
    session = String(req.params.id).replace('unregistered:', '');
  } else {
    res.status(404).json({ error: 'Agent not found' }); return;
  }

  // Menus are answered through the VPS gateway only (it holds the detector +
  // policy). Mac agents have no gateway seam for keys yet.
  if (machine !== 'vps') {
    res.status(400).json({ error: 'agent-key only supported for VPS agents' });
    return;
  }

  try {
    const r = await gatewayKey(session, String(key), !!confirm);
    res.status(r.status).json(r.body);
  } catch (err: any) {
    res.status(502).json({ ok: false, error: 'gateway unreachable', detail: err.message });
  }
});

// GET /api/agents/:id/prompt — read agent's system prompt file
router.get('/:id/prompt', (req: Request, res: Response) => {
  const registry = getRegistry() as any;
  const agent = registry?.agents?.[String(req.params.id)];
  const promptPath = agent?.system_prompt || `prompts/${req.params.id}.md`;
  const fullPath = join(process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra'), promptPath);
  try {
    const content = readFileSync(fullPath, 'utf-8');
    res.json({ content, path: promptPath });
  } catch {
    res.json({ content: '', path: promptPath, error: 'File not found' });
  }
});

// PUT /api/agents/:id/prompt — update agent's system prompt file
router.put('/:id/prompt', (req: Request, res: Response) => {
  const registry = getRegistry() as any;
  const agent = registry?.agents?.[String(req.params.id)];
  // Block edits to T0 and always_on agents
  if (agent?.tier === 'T0' || agent?.always_on) {
    res.status(403).json({ error: 'This agent\'s prompt is protected' });
    return;
  }
  const promptPath = agent?.system_prompt || `prompts/${req.params.id}.md`;
  const fullPath = join(process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra'), promptPath);
  try {
    const { content } = req.body;
    if (typeof content !== 'string') { res.status(400).json({ error: 'content required' }); return; }
    writeFileSync(fullPath, content, 'utf-8');
    res.json({ saved: true, path: promptPath });
  } catch (err: any) {
    res.status(500).json({ error: 'Failed to save', detail: err.message });
  }
});

// POST /api/agents/:id/task — structured task to inbox
router.post('/:id/task', (req: Request, res: Response) => {
  try {
    const { task, priority, context } = req.body;
    if (!task) { res.status(400).json({ error: 'task required' }); return; }
    const validPriorities = ['low', 'normal', 'high'];
    const prio = validPriorities.includes(priority) ? priority : 'normal';
    const agentId = req.params.id as string;
    const inboxDir = join(process.env.ORCHESTRA_DIR!, 'queue', 'inbox', agentId);
    if (!existsSync(inboxDir)) mkdirSync(inboxDir, { recursive: true });
    const filename = `${Date.now()}_task_dashboard.json`;
    const msg = {
      id: `task_${Date.now()}`,
      type: 'task_request',
      from: 'operator-dashboard',
      to: agentId,
      subject: task.slice(0, 100),
      body: task,
      priority: prio,
      context: context || null,
      source: 'dashboard',
      created: new Date().toISOString()
    };
    writeFileSync(join(inboxDir, filename), JSON.stringify(msg, null, 2));
    res.json({ sent: true, file: filename, priority: prio });
  } catch (err) {
    res.status(500).json({ error: 'Task failed', detail: String(err) });
  }
});

// GET /api/agents/:id/messages — recent inbox/outbox messages
router.get('/:id/messages', (req: Request, res: Response) => {
  try {
    const agentId = req.params.id as string;
    const orchestraDir = process.env.ORCHESTRA_DIR!;
    const messages: Record<string, unknown>[] = [];

    // Read inbox messages
    const inboxDir = join(orchestraDir, 'queue', 'inbox', agentId);
    if (existsSync(inboxDir)) {
      const inboxFiles = readdirSync(inboxDir).filter(f => f.endsWith('.json'));
      for (const file of inboxFiles) {
        try {
          const raw = readFileSync(join(inboxDir, file), 'utf-8');
          const parsed = JSON.parse(raw);
          parsed._source_dir = 'inbox';
          messages.push(parsed);
        } catch {}
      }
    }

    // Read outbox messages — scan last 3 days of date-based subdirectories
    const outboxDir = join(orchestraDir, 'queue', 'outbox');
    if (existsSync(outboxDir)) {
      const now = new Date();
      const dateDirs: string[] = [];
      for (let i = 0; i < 3; i++) {
        const d = new Date(now);
        d.setDate(d.getDate() - i);
        const dateStr = d.toISOString().slice(0, 10); // YYYY-MM-DD
        dateDirs.push(dateStr);
      }
      for (const dateDir of dateDirs) {
        const dirPath = join(outboxDir, dateDir);
        if (!existsSync(dirPath)) continue;
        const files = readdirSync(dirPath).filter(f => f.endsWith('.json'));
        for (const file of files) {
          try {
            const raw = readFileSync(join(dirPath, file), 'utf-8');
            const parsed = JSON.parse(raw);
            if (parsed.from === agentId || parsed.to === agentId) {
              parsed._source_dir = 'outbox';
              messages.push(parsed);
            }
          } catch {}
        }
      }
    }

    // Sort by created timestamp descending, return last 20
    messages.sort((a, b) => {
      const ta = (a.created as string) || '';
      const tb = (b.created as string) || '';
      return tb.localeCompare(ta);
    });

    const limited = messages.slice(0, 20);
    res.json({ messages: limited, total: messages.length });
  } catch (err) {
    res.status(500).json({ error: 'Failed to read messages', detail: String(err) });
  }
});

export default router;
