/**
 * agents-new.ts — the server half of Overview's "New Agent" button.
 *
 *   POST /api/agents/new          {name, task?, runtime?, parent?, gm?}
 *       parent: the registered seat the new one reports to. Omitted, the seat starts as a
 *          lead (T1) with no parent; under a T0 it is a T1, under a lead it is that lead's T2
 *          (scripts/tier_rule.py). An unregistered parent is a 400, never a spawn.
 *   GET  /api/agents/new/parents  -> {agents: [{id, tier}]} the seats a new one can report to
 *       -> a REAL registered seat through the repo's spawn-agent.sh (the same path
 *          Arturo's commission tool uses: AGENT_RUNTIME + AGENT_MODEL so the registry
 *          writer accepts a new row), verified by `tmux has-session`.
 *          409 no_authed_runtime when no CLI is installed AND logged in — that is the
 *          signal the button uses to fall back to the login shell below.
 *   POST /api/agents/login-shell  {}
 *       -> a plain bash tmux session (no agent, no registry row) that prints how to log
 *          in; the browser attaches to it with the existing /ws/terminal view.
 *
 * Everything is pure over injected deps (probe / spawn / tmux), so the tests never touch
 * a real CLI, tmux, or the registry.
 */
import { Router, type Request, type Response } from 'express';
import { execFile, execFileSync } from 'node:child_process';
import { getRuntimesForAuth, type ProviderResult } from './runtimes-available.js';
import { CODE_ROOT } from '../lib/codeRoot.js';

/** A catalog row as this route needs it. GET /api/runtimes/available does not expose the
 *  binary name, so `cli` is optional and falls back to the provider id (they match for
 *  claude/codex; gemini's binary is `agy`). */
export type RuntimeRow = ProviderResult & { cli?: string };
const CLI_FOR_ID: Record<string, string> = { claude: 'claude', gemini: 'agy', codex: 'codex', grok: 'grok' };
const cliOf = (r: RuntimeRow): string => r.cli || CLI_FOR_ID[r.id] || r.id;
// state-reader / tmux-monitor are imported LAZILY in the production deps below: they read
// orchestra.toml at module load, which would make this file un-importable in a unit test.

const ORCHESTRA = process.env.ORCHESTRA_DIR || process.env.HOME || '.';
const ORCHESTRA_ROOT = CODE_ROOT;   // the checkout (lib/codeRoot.ts), never the data dir
const MAX_NAME = 64;
// Sentinels only. `gm` is NOT one: docs/INSTALL.md tells a new operator to run
// `orchestra spawn gm --gm`, the CLI allows it, and a fresh install has no gm at all — so the
// web refusing the name blocked the product's own first instruction (Shaw, 2026-09-22).
const RESERVED = new Set(['all', 'none', 'new', 'arturo', 'self', 'system']);

// A new registry row must carry a resolvable runtime AND model or the flock-safe writer
// refuses it (spawn-agent.sh R4 invariant). Same table Arturo's commission path uses.
// A new Claude seat starts on Opus 5.5 with the 1M context (product decision 2026-10-22);
// claude-sonnet-5 was an old default.
export const DEFAULT_MODEL_FOR_RUNTIME: Record<string, string> = {
  claude: 'claude-opus-5-5[1m]',
  gemini: 'gemini-3.1-pro',
  codex: 'gpt-5.6-terra',
  grok: 'grok-4.7',
};

export interface NewAgentDeps {
  probeRuntimes: () => RuntimeRow[];
  existingNames: () => Set<string> | Promise<Set<string>>;
  spawn: (args: { name: string; task: string; runtime: string; gm: boolean; parent: string | null }) => Promise<{ ok: boolean; output: string }>;
  /** Registered seats (registry.json) and their tier: who a new seat may report to. */
  registeredAgents: () => Record<string, string> | Promise<Record<string, string>>;
  sessionExists: (session: string) => boolean;
  startLoginShell: (args: { session: string; greeting: string }) => Promise<{ ok: boolean; output: string }>;
}

export function normalizeAgentName(raw: unknown): { name: string; error?: string } {
  const text = String(raw ?? '').trim();
  const name = text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  if (!name) return { name: '', error: 'name_required' };
  if (text.length > MAX_NAME || name.length > MAX_NAME) return { name, error: 'name_too_long' };
  if (RESERVED.has(name)) return { name, error: 'name_reserved' };
  return { name };
}

/** A blue-green copy of a registered seat runs as `<root>-g<N>` (or `<root>-gen<N>`), and the
 *  registry never lists it before it exists. A new agent may not take such a name: the copy's own
 *  spawn would then collide with it. Returns the root it would shadow, or null. */
export function shadowedRoot(name: string, registered: Record<string, unknown>): string | null {
  const m = /^(.+)-(?:g|gen)\d+$/.exec(name);
  return m && Object.prototype.hasOwnProperty.call(registered, m[1]) ? m[1] : null;
}

/** First authed runtime (catalog order = the operator's [runtimes] enabled order), or the
 *  requested one when it is authed. `unverified` is never treated as usable (W1). */
export function pickRuntime(rows: RuntimeRow[], requested: string | undefined): { id: string; cli: string } | null {
  const authed = (rows || []).filter((r) => r.installed && r.authed === true);
  const row = requested ? authed.find((r) => r.id === requested) : authed[0];
  return row ? { id: row.id, cli: cliOf(row) } : null;
}

/** What to tell someone who has no logged-in CLI, keyed to what is actually on the box. */
/** The "Add a provider" shell. NOT a login for a provider that already exists: on a box
 *  where claude is installed, the no-provider form greets with "claude is installed but not
 *  logged in", which is the wrong errand when the operator came to ADD one. `install` is a
 *  purpose, not a cli, so the reused session is `login-install`. */
export function installShellHint(rows: RuntimeRow[]): { cli: string; hint: string; greeting: string } {
  const missing = (rows || []).filter((r) => !r.installed).map((r) => cliOf(r));
  const named = missing.length ? ` Not installed here yet: ${missing.join(', ')}.` : '';
  const hint = `Install a provider CLI on this machine, then run it once to sign in.${named}`;
  return {
    cli: 'install',
    hint,
    greeting: `Install a provider CLI here, then run it once to sign in.${named}\n\nThe sheet re-probes every time it opens, so once a CLI is installed and signed in it turns selectable.`,
  };
}

export function loginHint(rows: RuntimeRow[], providerId?: string): { cli: string; hint: string; greeting: string } {
  const installed = (rows || []).filter((r) => r.installed);
  // G21 — the disconnected-provider modal opens a login for the tile the operator TAPPED.
  // Without this the shell always targeted `installed[0]`, so tapping Gemini on a box where
  // Claude was installed opened a CLAUDE login and connected the wrong provider silently.
  const requested = providerId ? (rows || []).find((r) => r.id === providerId) : undefined;
  if (requested && !requested.installed) {
    const rcli = cliOf(requested);
    const hint = `\`${rcli}\` is not installed on this machine. Install it, then run it once and log in.`;
    return { cli: rcli, hint, greeting: `${hint}\n\nInstall it here, run it once to log in, then close this window and try again.` };
  }
  const cli = requested ? cliOf(requested) : (installed[0] ? cliOf(installed[0]) : 'claude');
  // --device-auth, never a bare `codex login`: the plain form opens a callback server on
  // port 1455 of THIS machine and waits for a browser to reach it, which is impossible when
  // the operator's browser is elsewhere. Device auth prints a code instead.
  // grok: the same reason — `grok login --device-auth` prints a link + code for a phone.
  const loginCmd = cli === 'codex' ? 'codex login --device-auth' : cli === 'grok' ? 'grok login --device-auth' : cli;
  if (!requested && installed.length === 0) {
    const hint = 'No agent CLI is installed on this machine yet. Install one (e.g. `npm i -g @anthropic-ai/claude-code`), then run it once and log in.';
    return { cli, hint, greeting: `${hint}\n\nInstall one here, run it once to log in, then close this window and try again.` };
  }
  const extra = cli === 'codex' || cli === 'grok' ? '' : ' then type /login';
  const hint = `\`${cli}\` is installed but not logged in. Run \`${loginCmd}\`${extra} to sign in to your provider.`;
  return {
    cli,
    hint,
    // Neutral last line: this shell is reached from BOTH the New Agent button and the
    // disconnected-provider modal, so naming one of them is wrong half the time.
    greeting: `You are not signed in to an AI provider yet — log in here, then come back.\n\nRun:  ${loginCmd}${extra ? '   (' + extra.trim() + ')' : ''}\n\nWhen it confirms you are signed in, close this window and try again.`,
  };
}

/** Role + first task -> the one --task string spawn-agent.sh takes. Either may be empty. */
export function composeTask(role: string, task: string): string {
  const r = role.trim(); const t = task.trim();
  if (r && t) return `Your role: ${r}.\n\n${t}`;
  if (r) return `Your role: ${r}.`;
  return t;
}

export function createAgentsNewRouter(deps: NewAgentDeps): Router {
  const router = Router();

  router.post('/new', async (req: Request, res: Response) => {
    const { name, error } = normalizeAgentName(req.body?.name);
    if (error) return res.status(400).json({ ok: false, reason: error, name });

    const rows = deps.probeRuntimes();
    const runtime = pickRuntime(rows, req.body?.runtime ? String(req.body.runtime) : undefined);
    if (!runtime) {
      return res.status(409).json({ ok: false, reason: 'no_authed_runtime', runtimes: rows });
    }
    const taken = await deps.existingNames();
    if (taken.has(name)) {
      return res.status(409).json({ ok: false, reason: 'name_taken', name });
    }
    const shadows = shadowedRoot(name, await deps.registeredAgents());
    if (shadows) {
      return res.status(409).json({ ok: false, reason: 'name_reserved_for_copy', name, root: shadows });
    }

    // The registry has no role column; a role is the first line of the seat's first task, which
    // is what spawn-agent.sh --task delivers to the new pane.
    const role = String(req.body?.role ?? '').trim().slice(0, 120);
    const firstTask = String(req.body?.task ?? '').trim();
    const task = composeTask(role, firstTask);
    // The General Manager is a different KIND of seat: tier T0, always-on, prompts/gm.md
    // (docs/REFERENCE_INSTALL.md: T0 is the always-on manager seat). Only `orchestra spawn --gm`
    // sets those, so the gm path goes through the CLI rather than spawn-agent.sh.
    const gm = req.body?.gm === true;
    // The parent is a REAL registered seat or nothing. This used to be the literal 'dashboard',
    // which is not an agent, so spawn-agent.sh's tier rule refused every Worker the form made
    // (Shaw on the workbench, 2026-10-11). A GM has no parent: it is the top of the tree.
    const parent = gm ? null : (String(req.body?.parent ?? '').trim() || null);
    if (parent) {
      const known = await deps.registeredAgents();
      // The same seats GET /new/parents lists: a copy of a registered seat is not a parent (codex R2 on #477).
      if (!Object.prototype.hasOwnProperty.call(known, parent) || shadowedRoot(parent, known)) {
        return res.status(400).json({ ok: false, reason: 'unknown_parent', parent });
      }
    }
    const result = await deps.spawn({ name, task, runtime: runtime.id, gm, parent });
    if (!result.ok) {
      return res.status(502).json({ ok: false, reason: 'spawn_failed', detail: result.output.slice(-600) });
    }
    if (!deps.sessionExists(name)) {
      return res.status(502).json({ ok: false, reason: 'session_missing', detail: result.output.slice(-600) });
    }
    return res.json({ ok: true, id: name, session: name, runtime: runtime.id, task, gm, parent });
  });

  router.get('/new/parents', async (_req: Request, res: Response) => {
    const known = await deps.registeredAgents();
    // Seats only: a blue-green copy or an earlier generation (`<root>-g<N>`, `<root>-gen<N>`) is
    // not one, and a new agent cannot report to it (ios-watch-dev: 382 of 534 rows were copies).
    const rows: Array<{ id: string; tier: string; default?: boolean }> = Object.entries(known)
      .filter(([id]) => !shadowedRoot(id, known))
      .map(([id, tier]) => ({ id, tier }))
      .sort((a, b) => a.id.localeCompare(b.id));
    // The fleet manager is the default a client offers for a lead: the T0 named gm, else the
    // only T0. Marked here so no client guesses it from names; none when it is ambiguous.
    const t0 = rows.filter((r) => r.tier === 'T0');
    const def = rows.find((r) => r.id === 'gm' && r.tier === 'T0') || (t0.length === 1 ? t0[0] : undefined);
    if (def) def.default = true;
    return res.json({ agents: rows });
  });

  router.post('/login-shell', async (req: Request, res: Response) => {
    const rows = deps.probeRuntimes();
    // {provider} is optional: the modal sends the tapped provider, the older New-Agent path
    // sends nothing and keeps its previous behaviour.
    const provider = req.body?.provider ? String(req.body.provider).slice(0, 40) : undefined;
    // {purpose:'install'} is the "Add a provider" tile: no provider chosen yet. A named
    // provider still wins — it is the more specific request of the two.
    const wantsInstall = !provider && req.body?.purpose === 'install';
    const { cli, hint, greeting } = wantsInstall ? installShellHint(rows) : loginHint(rows, provider);
    // ONE session per cli, reused: tapping twice must not leave a litter of shells.
    const session = `login-${cli}`;
    const started = await deps.startLoginShell({ session, greeting });
    if (!started.ok) {
      // LOG it, don't only return it. The detail rode home in the response body and nowhere
      // else, so a 502 the operator hit left no trace on the box: triage afterwards was
      // guesswork over a log that had never seen the failure (2026-09-29).
      const detail = started.output.slice(-400);
      console.warn(`[login-shell] shell_failed session=${session} cli=${cli} detail=${detail.replace(/\n/g, ' | ')}`);
      return res.status(502).json({ ok: false, reason: 'shell_failed', detail });
    }
    return res.json({ ok: true, session, cli, hint, machine: 'vps' });
  });

  return router;
}

// --- production deps -------------------------------------------------------------------

async function realExistingNames(): Promise<Set<string>> {
  const names = new Set<string>();
  try {
    const { getRegistry } = await import('../services/state-reader.js');
    for (const id of Object.keys(getRegistry()?.agents || {})) names.add(id);
  } catch { /* registry unreadable — tmux below still guards */ }
  try {
    const { getTmuxSessionNames } = await import('../services/tmux-monitor.js');
    for (const s of getTmuxSessionNames()) names.add(s);
  } catch { /* no tmux — the spawn itself will fail loud */ }
  return names;
}

function realSessionExists(session: string): boolean {
  try {
    execFileSync('tmux', ['has-session', '-t', session], { timeout: 5000, stdio: 'ignore' });
    return true;
  } catch { return false; }
}

/** A registry row that is an earlier generation of a seat, not a seat: replaced (succeeded_by /
 *  superseded_at) or retired. It is no one's parent and does not reserve copy names. */
export function isEarlierGeneration(row: Record<string, unknown> | null | undefined): boolean {
  return !!(row && (row.superseded_at || row.succeeded_by || String(row.status ?? '') === 'retired'));
}

async function realRegisteredAgents(): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  try {
    const { getRegistry } = await import('../services/state-reader.js');
    return registeredFromRegistry((getRegistry()?.agents || {}) as Record<string, Record<string, unknown>>);
  } catch { /* registry unreadable: no parents to offer */ }
  return out;
}

/** registry.json's agents -> {seat id: TIER}, leaving out earlier generations (the rows GET
 *  /new/parents and POST /new both work from). */
export function registeredFromRegistry(agents: Record<string, Record<string, unknown>>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [id, row] of Object.entries(agents || {})) {
    if (isEarlierGeneration(row)) continue;
    out[id] = String(row?.tier ?? '').toUpperCase();
  }
  return out;
}

/** The command line a New Agent spawn runs. `orchestra spawn <seat> --gm` registers T0 +
 *  always_on + prompts/gm.md and THEN calls spawn-agent.sh itself; the plain path calls
 *  spawn-agent.sh directly. The gm path must name the runtime: `orchestra spawn` takes it from
 *  --runtime, never from AGENT_RUNTIME, so without the flag a General Manager picked as Grok
 *  was registered and launched as the first enabled runtime (claude). */
export function spawnArgv({ name, task, runtime, gm }: { name: string; task: string; runtime: string; gm: boolean }): string[] {
  const args = gm
    ? [`${ORCHESTRA_ROOT}/bin/orchestra`, 'spawn', name, '--gm', '--runtime', runtime]
    : [`${ORCHESTRA_ROOT}/spawn-agent.sh`, name];
  if (task) args.push('--task', task);
  return args;
}

/** The environment a New Agent spawn runs with: the API's own, plus the runtime and its default
 *  model (what spawn-agent.sh hands the CLI as --model), with the parent set only when given. */
export function spawnEnv(base: NodeJS.ProcessEnv, runtime: string, parent: string | null): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    ...base,
    AGENT_RUNTIME: runtime,
    AGENT_MODEL: DEFAULT_MODEL_FOR_RUNTIME[runtime] || runtime,
  };
  // Unset, not inherited, when there is no parent: the API's own env must never pick one.
  delete env.PARENT_AGENT_ID;
  if (parent) env.PARENT_AGENT_ID = parent;
  return env;
}

function realSpawn({ name, task, runtime, gm, parent }: { name: string; task: string; runtime: string; gm: boolean; parent: string | null }): Promise<{ ok: boolean; output: string }> {
  const args = spawnArgv({ name, task, runtime, gm });
  const env = spawnEnv(process.env, runtime, parent);
  return new Promise((resolve) => {
    execFile(gm ? args[0] : 'bash', gm ? args.slice(1) : args, { timeout: 120000, cwd: ORCHESTRA_ROOT, env }, (err, stdout, stderr) => {
      resolve({ ok: !err, output: `${stdout || ''}\n${stderr || ''}`.trim() });
    });
  });
}

function realStartLoginShell({ session, greeting }: { session: string; greeting: string }): Promise<{ ok: boolean; output: string }> {
  return new Promise((resolve) => {
    // Idempotent: attach-able session either way. `printf` writes the hint into the pane's
    // scrollback, then an interactive bash takes over — a blank shell, not an agent.
    // %b, not %s: the greeting arrives with literal \n escapes (it went through
    // JSON.stringify to reach the shell) and must render as real newlines in the pane.
    const script = `tmux has-session -t ${JSON.stringify(session)} 2>/dev/null || ` +
      `tmux new-session -d -s ${JSON.stringify(session)} ` +
      `bash -lc ${JSON.stringify(`printf '%b\\n\\n' ${JSON.stringify(greeting)}; exec bash -i`)}`;
    execFile('bash', ['-lc', script], { timeout: 15000, cwd: process.env.HOME }, (err, stdout, stderr) => {
      resolve({ ok: !err, output: `${stdout || ''}\n${stderr || ''}`.trim() });
    });
  });
}

export const productionDeps: NewAgentDeps = {
  // Installed/authed only, from the shared cache when it is warm. Paying for a live model
  // probe here made opening a terminal a 3.5s round trip — a 502 behind a proxy.
  probeRuntimes: () => getRuntimesForAuth(),
  existingNames: realExistingNames,
  spawn: realSpawn,
  registeredAgents: realRegisteredAgents,
  sessionExists: realSessionExists,
  startLoginShell: realStartLoginShell,
};

export default createAgentsNewRouter(productionDeps);
