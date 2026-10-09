/**
 * The chat pane must never open a PREDECESSOR's (or another seat's) transcript.
 *
 * Found on a live fleet: state/agents/<id>.json was re-emitted every projector cycle with the
 * predecessor generation's session id (gm's blob named generation 91 while the canonical head was
 * generation 99), and the transcript file still existed, so `existsSync` passed and the web opened
 * the old seat's conversation. A reused tmux pane id can likewise carry another session's hook file.
 * A session the identity store knows is now refused unless it is this seat's current head.
 *
 * Run: npx tsx --test src/routes/chat-transcript.predecessor.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const SEAT = 'zz-transcript-seat';            // never a real tmux session on any box
const OLD = '11111111-2222-4333-8444-000000000001';
const CUR = '11111111-2222-4333-8444-000000000002';
const OTHER = '11111111-2222-4333-8444-000000000003';
const RETIRED = '11111111-2222-4333-8444-000000000004';

const root = mkdtempSync(join(tmpdir(), 'transcript-pred-'));
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;

function store() {
  const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
  db.exec(`CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
             always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
           CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
             conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
             promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
           CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run(SEAT, '/repo');
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run('zz-other-seat', '/repo');
  db.prepare("INSERT INTO generations (id, root, generation, session_id, promoted_at, retired_at) VALUES (1, ?, 1, ?, 'a', 'b')").run(SEAT, OLD);
  db.prepare("INSERT INTO generations (id, root, generation, session_id, promoted_at) VALUES (2, ?, 2, ?, 'b')").run(SEAT, CUR);
  db.prepare("INSERT INTO generations (id, root, generation, session_id, promoted_at) VALUES (3, 'zz-other-seat', 1, ?, 'c')").run(OTHER);
  db.prepare("INSERT INTO canonical VALUES (?, 2, ?, 'online')").run(SEAT, SEAT);
  db.prepare("INSERT INTO canonical VALUES ('zz-other-seat', 3, 'zz-other-seat', 'online')").run();
  // a service pane (a proxy, a router): a lineage with a canonical row whose generation has no session
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run('zz-service', '/repo');
  db.prepare("INSERT INTO generations (id, root, generation, session_id, promoted_at) VALUES (4, 'zz-service', 1, NULL, 'd')").run();
  db.prepare("INSERT INTO canonical VALUES ('zz-service', 4, 'zz-service', 'online')").run();
  // a retired lineage: sessions in the store, no canonical row
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run('zz-retired', '/repo');
  db.prepare("INSERT INTO generations (id, root, generation, session_id, promoted_at, retired_at) VALUES (5, 'zz-retired', 1, ?, 'e', 'f')").run(RETIRED);
  db.close();
}
store();

function transcript(sid: string, ageS: number) {
  const p = join(projects, `${sid}.jsonl`);
  writeFileSync(p, '{}\n');
  const t = Date.now() / 1000 - ageS;
  utimesSync(p, t, t);
}
transcript(CUR, 300);
transcript(OLD, 10);           // the predecessor's file was written LAST
transcript(OTHER, 5);          // and another seat sharing the cwd wrote even later

// imported after HOME / ORCHESTRA_DIR point at the fixture
const { resolveTranscriptPath, sessionIsThisSeats } = await import('./chat-transcript.js');

test('a stale state blob naming the predecessor does not open its transcript', () => {
  writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: OLD, cwd: '/repo' }));
  writeFileSync(join(orch, 'state', 'agent-sessions.json'), JSON.stringify({ [SEAT]: { session_id: OLD, cwd: '/repo' } }));
  const r = resolveTranscriptPath(SEAT);
  assert.equal(r.sid, CUR, 'the current head, not the predecessor written later');
});

test('the shared project dir never yields another seat\'s or a predecessor\'s newest file', () => {
  writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({}));
  writeFileSync(join(orch, 'state', 'agent-sessions.json'), JSON.stringify({ [SEAT]: { cwd: '/repo' } }));
  assert.equal(resolveTranscriptPath(SEAT).sid, CUR);
});

test('the rule, by owner', () => {
  const owners = new Map<string, { root: string; current: boolean }>([
    [OLD, { root: SEAT, current: false }], [CUR, { root: SEAT, current: true }],
    [OTHER, { root: 'zz-other-seat', current: true }],
    ['green-sid', { root: `${SEAT}-g3`, current: true }]]);
  assert.equal(sessionIsThisSeats(SEAT, CUR, owners), true);
  assert.equal(sessionIsThisSeats(SEAT, OLD, owners), false, 'a predecessor');
  assert.equal(sessionIsThisSeats(SEAT, OTHER, owners), false, 'another seat');
  assert.equal(sessionIsThisSeats(SEAT, 'unknown-sid', owners), true, 'a /clear or a seat outside the store');
  assert.equal(sessionIsThisSeats(SEAT, 'green-sid', owners), true,
               'a promoted green still filed under its shadow root is this seat\'s lineage');
  assert.equal(sessionIsThisSeats(`${SEAT}-g1`, OLD, owners), true, 'a generation id may show its lineage');
  assert.equal(sessionIsThisSeats(`${SEAT}-g1`, OTHER, owners), false);
  assert.equal(sessionIsThisSeats(SEAT, OLD, null), true, 'no store: as before');
});

test('the fallback never picks a subagent sidechain or a session the store does not know, for a known seat', () => {
  transcript('agent-a15c9fa', 1);                            // a subagent sidechain, newest of all
  transcript('99999999-2222-4333-8444-000000000009', 2);     // a headless job the store does not know
  writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({}));
  writeFileSync(join(orch, 'state', 'agent-sessions.json'), JSON.stringify({ [SEAT]: { cwd: '/repo' } }));
  assert.equal(resolveTranscriptPath(SEAT).sid, CUR);
});

test('a lineage the store knows with no session yet (a service pane) never takes someone else\'s newest file', () => {
  transcript('99999999-2222-4333-8444-00000000000a', 0);     // a headless job the store does not know, newest
  writeFileSync(join(orch, 'state', 'agent-sessions.json'), JSON.stringify({ 'zz-service': { cwd: '/repo' } }));
  assert.equal(resolveTranscriptPath('zz-service').path, null);
});

test('a retired lineage (sessions in the store, no canonical row) never takes someone else\'s newest file', () => {
  transcript('99999999-2222-4333-8444-00000000000b', 0);     // a headless job the store does not know, newest
  writeFileSync(join(orch, 'state', 'agent-sessions.json'), JSON.stringify({ 'zz-retired': { cwd: '/repo' } }));
  assert.equal(resolveTranscriptPath('zz-retired').path, null);
});
