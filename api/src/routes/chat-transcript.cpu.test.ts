/**
 * Call site for the lower-API-CPU change: resolveTranscriptPath no longer forks tmux + pgrep on every
 * poll (ported from the private fleet's a372e813c5; the public route also has a live-Grok probe, which
 * now reads the same cached pane and /proc).
 *
 * - The Gemini probe runs ONLY for a seat that can be Gemini (runtime gemini/agy, a gemini-/agy- name,
 *   or no recorded runtime). The claude seat's pane here even HAS a Gemini child, so if the probe ran,
 *   the claude seat would resolve to the Gemini file: the result proves which path ran.
 * - A live Grok child is still found, through the cache and /proc, with no fork.
 * - Polls inside the TTL reuse one tmux call per session.
 *
 * Run: npx tsx --test src/routes/chat-transcript.cpu.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const root = mkdtempSync(join(tmpdir(), 'transcript-cpu-'));
const home = join(root, 'home');
const orch = join(root, 'orch');
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
delete process.env.GROK_HOME;

const CLAUDE_SEAT = 'zz-claude-seat';
const GEMINI_SEAT = 'zz-gemini-seat';
const GROK_SEAT = 'zz-grok-seat';
const CLAUDE_SID = '0199aaaa-0000-7000-8000-0000000000c1';
const GEMINI_CID = 'aaaaaaaa-0000-4000-8000-00000000000a';
const GROK_SID = 'bbbbbbbb-0000-4000-8000-00000000000b';

mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
mkdirSync(join(orch, 'state', 'agent-events', 'panes'), { recursive: true });
const project = join(home, '.claude', 'projects', '-repo');
mkdirSync(project, { recursive: true });
writeFileSync(join(project, `${CLAUDE_SID}.jsonl`), '{"type":"user"}\n');
const brain = join(home, '.gemini', 'antigravity-cli', 'brain', GEMINI_CID, '.system_generated', 'logs');
mkdirSync(brain, { recursive: true });
writeFileSync(join(brain, 'transcript.jsonl'), '{}\n');
const grokDir = join(home, '.grok', 'sessions', 'repo', GROK_SID);
mkdirSync(grokDir, { recursive: true });
writeFileSync(join(grokDir, 'updates.jsonl'), '{}\n');
// The claude pane's hook file (what 0b reads).
writeFileSync(join(orch, 'state', 'agent-events', 'panes', '7.json'),
  JSON.stringify({ session_id: CLAUDE_SID, cwd: '/repo' }));
// The seats' records: the route reads each seat's runtime from here.
writeFileSync(join(orch, 'state', 'agent-sessions.json'), JSON.stringify({
  [CLAUDE_SEAT]: { tmux_session: CLAUDE_SEAT, runtime: 'claude' },
  [GEMINI_SEAT]: { tmux_session: GEMINI_SEAT, runtime: 'gemini' },
  [GROK_SEAT]: { tmux_session: GROK_SEAT },
}));

const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
db.exec(`CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
           always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
         CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
           conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
           promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
         CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
db.prepare('INSERT INTO lineages (root, runtime, cwd) VALUES (?, ?, ?)').run(CLAUDE_SEAT, 'claude', '/repo');
db.prepare('INSERT INTO lineages (root, runtime, cwd) VALUES (?, ?, ?)').run(GEMINI_SEAT, 'gemini', '/repo');
db.prepare('INSERT INTO generations (id, root, generation, session_id) VALUES (1, ?, 1, ?)').run(CLAUDE_SEAT, CLAUDE_SID);
db.prepare('INSERT INTO generations (id, root, generation, session_id) VALUES (2, ?, 1, ?)').run(GEMINI_SEAT, GEMINI_CID);
db.prepare("INSERT INTO canonical VALUES (?, 1, ?, 'online')").run(CLAUDE_SEAT, CLAUDE_SEAT);
db.prepare("INSERT INTO canonical VALUES (?, 2, ?, 'online')").run(GEMINI_SEAT, GEMINI_SEAT);
db.close();

const { setPaneProbeDeps } = await import('../lib/paneProbe.js');
const { resolveTranscriptPath } = await import('./chat-transcript.js');

// The claude and gemini panes (pid 100) have a child holding a Gemini brain fd; the grok pane
// (pid 200) has a grok child bound to GROK_SID on its argv.
const PROCS: Record<string, { tasks: Record<string, string>; fds?: Record<string, string>; argv?: string[] }> = {
  '100': { tasks: { '100': '300' }, argv: ['bash'] },
  '300': { tasks: { '300': '' }, fds: { '4': `${home}/.gemini/antigravity-cli/brain/${GEMINI_CID}/x` }, argv: ['node'] },
  '200': { tasks: { '200': '400' }, argv: ['bash'] },
  '400': { tasks: { '400': '' }, argv: ['/home/u/.grok/bin/grok', '-s', GROK_SID] },
};
const PANES: Record<string, string> = { [CLAUDE_SEAT]: '100 %7 1\n', [GEMINI_SEAT]: '100 %8 1\n', [GROK_SEAT]: '200 %9 1\n' };
const calls = { tmux: 0, fdScans: 0 };
setPaneProbeDeps({
  tmux: (args) => { calls.tmux++; const s = PANES[args[args.indexOf('-t') + 1]]; if (!s) throw new Error('no session'); return s; },
  readText: (p) => {
    let m = p.match(/^\/proc\/(\d+)\/task\/(\d+)\/children$/);
    if (m && PROCS[m[1]]?.tasks[m[2]] !== undefined) return PROCS[m[1]].tasks[m[2]];
    m = p.match(/^\/proc\/(\d+)\/cmdline$/);
    if (m && PROCS[m[1]]?.argv) return PROCS[m[1]].argv!.join('\0') + '\0';
    throw new Error('ENOENT');
  },
  readDir: (p) => {
    let m = p.match(/^\/proc\/(\d+)\/task$/); if (m && PROCS[m[1]]) return Object.keys(PROCS[m[1]].tasks);
    m = p.match(/^\/proc\/(\d+)\/fd$/); if (m && PROCS[m[1]]) { calls.fdScans++; return Object.keys(PROCS[m[1]].fds || {}); }
    throw new Error('ENOENT');
  },
  readLink: (p) => { const m = p.match(/^\/proc\/(\d+)\/fd\/(\d+)$/); const v = m && PROCS[m[1]]?.fds?.[m[2]]; if (!v) throw new Error('ENOENT'); return v; },
  now: () => 5_000_000,
});

test('fence: scratch HOME and ORCHESTRA_DIR', () => {
  assert.ok(home.startsWith(tmpdir()) && orch.startsWith(tmpdir()));
});

test('a claude-runtime seat never runs the Gemini probe: resolves to its claude transcript, 0 fd scans', () => {
  const r = resolveTranscriptPath(CLAUDE_SEAT);
  assert.equal(r.sid, CLAUDE_SID);
  assert.equal(r.path, join(project, `${CLAUDE_SID}.jsonl`));
  assert.equal(calls.fdScans, 0);
});

test('a gemini-runtime seat still finds its live conversation from the pane', () => {
  const r = resolveTranscriptPath(GEMINI_SEAT);
  assert.equal(r.sid, GEMINI_CID);
  assert.equal(r.path, join(brain, 'transcript.jsonl'));
  assert.ok(calls.fdScans > 0);
});

test('a live grok child is found through the cached pane and /proc, with no pgrep', () => {
  const r = resolveTranscriptPath(GROK_SEAT);
  assert.equal(r.sid, GROK_SID);
  assert.equal(r.path, join(grokDir, 'updates.jsonl'));
});

test('polls inside the TTL reuse one tmux call per session (was tmux + pgrep forks per poll)', () => {
  const before = calls.tmux;
  for (let i = 0; i < 10; i++) {
    resolveTranscriptPath(CLAUDE_SEAT); resolveTranscriptPath(GEMINI_SEAT); resolveTranscriptPath(GROK_SEAT);
  }
  assert.equal(calls.tmux, before);
});
