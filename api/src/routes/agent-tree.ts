/**
 * Subagent tree of a seat. The JSON is scripts/agent_tree.py's contract:
 *   {agent_id, parent_agent_id, depth, type, label, state, started_at, last_activity_at, current_tool,
 *    last_tool, tokens_own, tokens_rollup, tokens_cache_read, ctx_tokens, elapsed_s, children[]}
 * over HTTP without transcript_path (absolute host paths; no client needs them), plus
 * `all_finished` on the root saying whether ?all=1 was honoured.
 *
 * GET /api/agents/:id/tree[?all=1]         snapshot (404 when the seat has no Claude session on this box)
 * GET /api/agents/:id/tree/stream[?all=1]  SSE: `event: snapshot` with the whole tree whenever any node
 *                                          changes; one shared `agent_tree.py --watch` child per seat
 *                                          (and per ?all), killed with its last client.
 * ?all=1 passes --all-finished: every node ever spawned, not just the last hour's finished ones.
 *
 * Scoped like the seat's own chat (router.param + agentScopeParam): who cannot see a seat cannot
 * see its tree. Mounted at /api/agents in server.ts.
 */
import { Router, type Request, type Response } from 'express';
import { execFile, spawn } from 'child_process';
import { existsSync } from 'fs';
import type { Readable } from 'stream';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { agentScopeParam } from '../lib/agent-scope.js';
import { codePath } from '../lib/codeRoot.js';

// Code, not data: the script ships in the checkout (lib/codeRoot.ts), never under ORCHESTRA_DIR.
export const TREE_PY = codePath('scripts', 'agent_tree.py');
const VENV_PY = codePath('.venv', 'bin', 'python');
const python = () => (existsSync(VENV_PY) ? VENV_PY : 'python3');
const WATCH_S = 2;
const HEARTBEAT_MS = 25000;

export interface WatchChild {
  stdout: Readable | null;
  on(ev: 'exit' | 'error', fn: () => void): unknown;
  kill(): unknown;
}

export interface TreeDeps {
  runTree(id: string, all: boolean): Promise<unknown | null>;
  spawnWatch(id: string, all: boolean): WatchChild;
}

/** argv for agent_tree.py: ?all=1 adds --all-finished; the stream child also gets --watch. */
export function treeArgs(id: string, all: boolean, watchS?: number): string[] {
  return [TREE_PY, id, '--json', ...(all ? ['--all-finished'] : []), ...(watchS ? ['--watch', String(watchS)] : [])];
}

export const defaultDeps: TreeDeps = {
  runTree: (id, all) => new Promise((resolve) => {
    execFile(python(), treeArgs(id, all), { timeout: 8000, maxBuffer: 16 << 20 },
      (_err, stdout) => {
        try { resolve(JSON.parse(String(stdout || 'null'))); } catch { resolve(null); }
      });
  }),
  spawnWatch: (id, all) => spawn(python(), treeArgs(id, all, WATCH_S),
    { stdio: ['ignore', 'pipe', 'ignore'] }),
};

type Node = Record<string, unknown> & { children?: Node[] };

/** The tree as clients get it: transcript_path dropped at every depth, all_finished on the root. */
export function publicTree(tree: unknown, all: boolean): unknown {
  if (!tree || typeof tree !== 'object') return tree ?? null;
  const strip = (n: Node): Node => {
    const { transcript_path: _drop, children, ...rest } = n;
    void _drop;
    return { ...rest, children: Array.isArray(children) ? children.map(strip) : [] };
  };
  return { ...strip(tree as Node), all_finished: all };
}

const wantsAll = (req: Request) => req.query?.all === '1';

export async function handleTree(req: Request, res: Response, deps: TreeDeps = defaultDeps): Promise<void> {
  const id = String(req.params.id);
  if (!isSafeAgentId(id)) { res.status(400).json({ error: 'bad agent id' }); return; }
  const all = wantsAll(req);
  const tree = await deps.runTree(id, all);
  if (!tree) { res.status(404).json({ error: 'no Claude session for this agent on this box' }); return; }
  res.json(publicTree(tree, all));
}

type Sub = (frame: string) => void;
interface Watch { child: WatchChild; subs: Set<Sub>; last: string | null; lastFrame: string | null; buf: string }
const watches = new Map<string, Watch>();
/** The last frame a subscriber gets when its seat's watcher is gone (exited, or never started). */
export const END_FRAME = 'event: end\ndata: {}\n\n';

function frameOf(line: string, all: boolean): string {
  let data = line;
  try { data = JSON.stringify(publicTree(JSON.parse(line), all)); } catch { /* not JSON: pass through */ }
  return `event: snapshot\ndata: ${data}\n\n`;
}

/** Subscribe to a seat's tree; returns the unsubscribe. Exported for tests. */
export function subscribeTree(id: string, sub: Sub, deps: TreeDeps = defaultDeps, all = false): () => void {
  const key = all ? `${id}\0all` : id;
  let w = watches.get(key);
  if (!w) {
    const child = deps.spawnWatch(id, all);
    const fresh: Watch = { child, subs: new Set(), last: null, lastFrame: null, buf: '' };
    w = fresh;
    watches.set(key, fresh);
    child.stdout?.setEncoding('utf8');
    child.stdout?.on('data', (chunk: string) => {
      fresh.buf += chunk;
      let nl: number;
      while ((nl = fresh.buf.indexOf('\n')) >= 0) {
        const line = fresh.buf.slice(0, nl).trim();
        fresh.buf = fresh.buf.slice(nl + 1);
        if (!line || line === fresh.last) continue;      // unchanged tree: nothing to send
        fresh.last = line;
        fresh.lastFrame = frameOf(line, all);
        for (const s of fresh.subs) { try { s(fresh.lastFrame); } catch { /* a dead client */ } }
      }
    });
    // 'exit', or 'error' when the child could not be spawned at all (ENOENT, EACCES, EMFILE): without an
    // 'error' listener that would be an unhandled event and take the whole API down. Either ends it once.
    let ended = false;
    const end = () => {
      if (ended) return;
      ended = true;
      if (watches.get(key) === fresh) watches.delete(key);
      for (const s of fresh.subs) { try { s(END_FRAME); } catch { /* gone */ } }
      fresh.subs.clear();
    };
    child.on('exit', end);
    child.on('error', end);
  }
  const watch = w;
  watch.subs.add(sub);
  if (watch.lastFrame) sub(watch.lastFrame);             // a late joiner gets the current tree at once
  return () => {
    watch.subs.delete(sub);
    if (watch.subs.size === 0 && watches.get(key) === watch) {
      watches.delete(key);
      try { watch.child.kill(); } catch { /* already gone */ }
    }
  };
}

export function liveWatchCount(): number { return watches.size; }

const router = Router();
router.param('id', agentScopeParam);

router.get('/:id/tree', (req, res) => { void handleTree(req, res); });

router.get('/:id/tree/stream', (req: Request, res: Response) => {
  const id = String(req.params.id);
  if (!isSafeAgentId(id)) { res.status(400).json({ error: 'bad agent id' }); return; }
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.write('retry: 3000\n\n');
  // The watcher gone: the end frame, then this response closes too (no heartbeat left on a dead stream).
  // The client falls back to polling on `end`.
  let heartbeat: NodeJS.Timeout | undefined;
  const unsubscribe = subscribeTree(id, (f) => {
    res.write(f);
    if (f === END_FRAME) { if (heartbeat) clearInterval(heartbeat); res.end(); }
  }, defaultDeps, wantsAll(req));
  if (res.writableEnded) return;
  heartbeat = setInterval(() => { res.write(': ping\n\n'); }, HEARTBEAT_MS);
  heartbeat.unref?.();
  req.on('close', () => { clearInterval(heartbeat); unsubscribe(); });
});

export default router;
