/**
 * agents-new.ts — the server half of Overview's "New Agent" button.
 *
 *   POST /api/agents/new          {name, task?, runtime?}
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
import { probeAll, makeDefaultDeps, type ProviderResult } from './runtimes-available.js';

/** A catalog row as this route needs it. GET /api/runtimes/available does not expose the
 *  binary name, so `cli` is optional and falls back to the provider id (they match for
 *  claude/codex; gemini's binary is `agy`). */
export type RuntimeRow = ProviderResult & { cli?: string };
const CLI_FOR_ID: Record<string, string> = { claude: 'claude', gemini: 'agy', codex: 'codex' };
const cliOf = (r: RuntimeRow): string => r.cli || CLI_FOR_ID[r.id] || r.id;
// state-reader / tmux-monitor are imported LAZILY in the production deps below: they read
// orchestra.toml at module load, which would make this file un-importable in a unit test.

const ORCHESTRA = process.env.ORCHESTRA_DIR || process.env.HOME || '.';
const ORCHESTRA_ROOT = process.env.ORCHESTRA_ROOT || ORCHESTRA;
const MAX_NAME = 64;
const RESERVED = new Set(['all', 'none', 'new', 'gm', 'arturo', 'self', 'system']);

// A new registry row must carry a resolvable runtime AND model or the flock-safe writer
// refuses it (spawn-agent.sh R4 invariant). Same table Arturo's commission path uses.
const DEFAULT_MODEL_FOR_RUNTIME: Record<string, string> = {
  claude: 'claude-sonnet-5',
  gemini: 'gemini-3.1-pro',
  codex: 'gpt-5.6-terra',
};

export interface NewAgentDeps {
  probeRuntimes: () => RuntimeRow[];
  existingNames: () => Set<string> | Promise<Set<string>>;
  spawn: (args: { name: string; task: string; runtime: string }) => Promise<{ ok: boolean; output: string }>;
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

/** First authed runtime (catalog order = the operator's [runtimes] enabled order), or the
 *  requested one when it is authed. `unverified` is never treated as usable (W1). */
export function pickRuntime(rows: RuntimeRow[], requested: string | undefined): { id: string; cli: string } | null {
  const authed = (rows || []).filter((r) => r.installed && r.authed === true);
  const row = requested ? authed.find((r) => r.id === requested) : authed[0];
  return row ? { id: row.id, cli: cliOf(row) } : null;
}

/** What to tell someone who has no logged-in CLI, keyed to what is actually on the box. */
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
  const loginCmd = cli === 'codex' ? 'codex login --device-auth' : cli;
  if (!requested && installed.length === 0) {
    const hint = 'No agent CLI is installed on this machine yet. Install one (e.g. `npm i -g @anthropic-ai/claude-code`), then run it once and log in.';
    return { cli, hint, greeting: `${hint}\n\nInstall one here, run it once to log in, then close this window and try again.` };
  }
  const extra = cli === 'codex' ? '' : ' then type /login';
  const hint = `\`${cli}\` is installed but not logged in. Run \`${loginCmd}\`${extra} to sign in to your provider.`;
  return {
    cli,
    hint,
    // Neutral last line: this shell is reached from BOTH the New Agent button and the
    // disconnected-provider modal, so naming one of them is wrong half the time.
    greeting: `You are not signed in to an AI provider yet — log in here, then come back.\n\nRun:  ${loginCmd}${extra ? '   (' + extra.trim() + ')' : ''}\n\nWhen it confirms you are signed in, close this window and try again.`,
  };
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

    const task = String(req.body?.task ?? '').trim();
    const result = await deps.spawn({ name, task, runtime: runtime.id });
    if (!result.ok) {
      return res.status(502).json({ ok: false, reason: 'spawn_failed', detail: result.output.slice(-600) });
    }
    if (!deps.sessionExists(name)) {
      return res.status(502).json({ ok: false, reason: 'session_missing', detail: result.output.slice(-600) });
    }
    return res.json({ ok: true, id: name, session: name, runtime: runtime.id, task });
  });

  router.post('/login-shell', async (req: Request, res: Response) => {
    const rows = deps.probeRuntimes();
    // {provider} is optional: the modal sends the tapped provider, the older New-Agent path
    // sends nothing and keeps its previous behaviour.
    const provider = req.body?.provider ? String(req.body.provider).slice(0, 40) : undefined;
    const { cli, hint, greeting } = loginHint(rows, provider);
    // ONE session per cli, reused: tapping twice must not leave a litter of shells.
    const session = `login-${cli}`;
    const started = await deps.startLoginShell({ session, greeting });
    if (!started.ok) {
      return res.status(502).json({ ok: false, reason: 'shell_failed', detail: started.output.slice(-400) });
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

function realSpawn({ name, task, runtime }: { name: string; task: string; runtime: string }): Promise<{ ok: boolean; output: string }> {
  const args = [`${ORCHESTRA_ROOT}/spawn-agent.sh`, name];
  if (task) args.push('--task', task);
  const env = {
    ...process.env,
    AGENT_RUNTIME: runtime,
    AGENT_MODEL: DEFAULT_MODEL_FOR_RUNTIME[runtime] || runtime,
    PARENT_AGENT_ID: 'dashboard',
  };
  return new Promise((resolve) => {
    execFile('bash', args, { timeout: 120000, cwd: ORCHESTRA_ROOT, env }, (err, stdout, stderr) => {
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
  probeRuntimes: () => probeAll(makeDefaultDeps()).providers,
  existingNames: realExistingNames,
  spawn: realSpawn,
  sessionExists: realSessionExists,
  startLoginShell: realStartLoginShell,
};

export default createAgentsNewRouter(productionDeps);
