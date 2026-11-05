/**
 * orchestra-report.ts — a Pi extension that tells OrchestraOS what this Pi session is doing.
 *
 * OrchestraOS reads an agent's state from its pane state file,
 * <data dir>/state/agent-events/panes/<pane number>.json (the same file Claude's hooks write), and
 * a report beats reading the screen. Pi has no prompt glyph OrchestraOS knows, so without this the
 * seat's state is a guess. This writes the file on each of Pi's lifecycle events: working while a
 * prompt runs, idle when it ends, stopped on exit, plus the session file (so a successor's briefing
 * can read the conversation), the model, and how full the context is.
 *
 * Install: copy (or symlink) into ~/.pi/agent/extensions/, or run `pi -e <path>` to try it.
 * Outside tmux it does nothing. It never throws into Pi: a report that cannot be written is skipped.
 */
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

type State = 'working' | 'idle' | 'stopped';

/** The parts of Pi's extension API this uses (@mariozechner/pi-coding-agent ExtensionAPI). */
export interface PiLike {
  on(event: string, handler: (event: any, ctx: any) => unknown): void;
}

export interface ReportEnv {
  pane?: string;
  dataDir: string;
  now?: () => number;
}

export function reportEnv(env: NodeJS.ProcessEnv = process.env): ReportEnv {
  return {
    pane: env.TMUX_PANE,
    dataDir: env.ORCHESTRA_DIR || env.ORCH_DIR || join(homedir(), '.orchestra'),
  };
}

/** The pane state file for a tmux pane id ("%12" -> panes/12.json), or null for anything else. */
export function reportPath(e: ReportEnv): string | null {
  if (!e.pane || !/^%\d+$/.test(e.pane)) return null;
  return join(e.dataDir, 'state', 'agent-events', 'panes', `${e.pane.slice(1)}.json`);
}

function modelId(m: any): string | undefined {
  return m && typeof m.id === 'string' ? (typeof m.provider === 'string' ? `${m.provider}/${m.id}` : m.id) : undefined;
}

function sessionFields(ctx: any): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  try {
    const sm = ctx?.sessionManager;
    const sid = sm?.getSessionId?.();
    const file = sm?.getSessionFile?.();
    const cwd = sm?.getCwd?.();
    if (typeof sid === 'string' && sid) out.session_id = sid;
    if (typeof file === 'string' && file) out.transcript_path = file;
    if (typeof cwd === 'string' && cwd) out.cwd = cwd;
  } catch { /* not every context has a session */ }
  try {
    const u = ctx?.getContextUsage?.();
    if (u && typeof u.contextWindow === 'number') {
      out.context = { tokens: u.tokens ?? null, window: u.contextWindow, percent: u.percent ?? null };
    }
  } catch { /* context usage is unknown right after a compaction */ }
  const m = modelId(ctx?.model);
  if (m) out.model = m;
  return out;
}

export function install(pi: PiLike, e: ReportEnv = reportEnv()): void {
  const path = reportPath(e);
  if (!path) return;
  const now = e.now ?? (() => Date.now() / 1000);
  let tool = '';

  const write = (event: string, state: State, ctx: any, extra: Record<string, unknown> = {}) => {
    try {
      const body = { pane: e.pane, runtime: 'pi', event, state, tool, ts: now(), ...sessionFields(ctx), ...extra };
      mkdirSync(join(path, '..'), { recursive: true });
      const tmp = `${path}.${process.pid}.tmp`;
      writeFileSync(tmp, JSON.stringify(body));
      renameSync(tmp, path);
    } catch { /* never break Pi over a report */ }
  };

  pi.on('session_start', (_ev, ctx) => { tool = ''; write('session_start', 'idle', ctx); });
  pi.on('agent_start', (_ev, ctx) => { tool = ''; write('agent_start', 'working', ctx); });
  pi.on('tool_execution_start', (ev, ctx) => {
    tool = typeof ev?.toolName === 'string' ? ev.toolName : '';
    write('tool_execution_start', 'working', ctx);
  });
  pi.on('turn_end', (_ev, ctx) => write('turn_end', 'working', ctx));
  pi.on('agent_end', (_ev, ctx) => { tool = ''; write('agent_end', 'idle', ctx); });
  pi.on('model_select', (ev, ctx) => {
    const m = modelId(ev?.model);
    write('model_select', ctx?.isIdle?.() === false ? 'working' : 'idle', ctx, m ? { model: m } : {});
  });
  pi.on('session_compact', (_ev, ctx) => write('session_compact', ctx?.isIdle?.() === false ? 'working' : 'idle', ctx));
  pi.on('session_shutdown', (_ev, ctx) => { tool = ''; write('session_shutdown', 'stopped', ctx); });
}

export default function (pi: PiLike) {
  install(pi);
}
