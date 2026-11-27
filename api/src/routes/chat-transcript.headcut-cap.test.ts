/**
 * The head-cut's cap (its review): before a generation's promotion only its machine-only hold phase is
 * cut (the wakes, the answers to them, the green's own init and baton reading). The first turn a person opened, even
 * before promoted_at, is the real takeover: from there on everything stays except machine wakes. That keeps an
 * operator's message to a green, and the real work of an old generation whose promotion was stamped late (13 of 439
 * generations on this box; pm-seat-b g15 ran four hours of the operator's work and his jobs' notices before its stamp).
 *
 * Run: npx tsx --test src/routes/chat-transcript.headcut-cap.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const SEAT = 'zz-headcut-cap-seat';             // never a real tmux session on any box
const OLD = '66666666-2222-4333-8444-000000000003';
const GREEN = '66666666-2222-4333-8444-000000000004';
const LATE = '2026-11-10T00:00:00+00:00';       // the old generation's promotion, stamped days after it started work
const SWAP = '2026-11-20T08:29:10.973388+00:00'; // the old generation retired, the green promoted

const root = testTmpDir('transcript-headcut-cap-');
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
process.env.MSG_DB_PATH = join(orch, 'no-tasks.db');   // no msg_store: nothing merged
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}

{
  const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
  db.exec(`CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
             always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
           CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
             conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
             promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
           CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run(SEAT, '/repo');
  const g = db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at, retired_at) VALUES (?,?,?,?,?,?)');
  g.run(1, SEAT, 3, OLD, LATE, SWAP);
  g.run(2, SEAT, 4, GREEN, SWAP, null);
  db.prepare("INSERT INTO canonical VALUES (?, 2, ?, 'online')").run(SEAT, SEAT);
  // The capsule's owed verdict for the green (generation row 2): nothing owed at its promotion, so its promote-wake turn
  // (g13, "I am now canonical") is left out (chat-transcript.headcut-promote.test.ts covers the verdict rules).
  mkdirSync(join(orch, 'state', 'agent-handoffs', 'owed'), { recursive: true });
  writeFileSync(join(orch, 'state', 'agent-handoffs', 'owed', `${SEAT}.2.json`),
    JSON.stringify({ schema: 'owed-verdict/1', root: SEAT, generation_id: 2, promoted_at: SWAP, owed: false, reasons: [] }));
  db.close();
}

const user = (uuid: string, ts: string, text: string) =>
  JSON.stringify({ type: 'user', uuid, timestamp: ts, message: { role: 'user', content: text } });
const say = (uuid: string, ts: string, text: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: ts,
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] },
});
const work = (uuid: string, ts: string, text: string): string[] => [
  JSON.stringify({
    type: 'assistant', uuid, timestamp: ts,
    message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [
      { type: 'text', text }, { type: 'tool_use', id: `t-${uuid}`, name: 'Bash', input: { command: 'true' } }] },
  }),
  JSON.stringify({ type: 'user', uuid: `${uuid}r`, timestamp: ts, message: { role: 'user', content: [
    { type: 'tool_result', tool_use_id: `t-${uuid}`, content: 'ok' }] } }),
];
const mail = (from: string, body: string) => `[MSG from ${from} | high] (sent 0m ago) ${body} — Read /tmp/agent-msg-x.md and act on it.`;
const d = (day: string, hms: string) => `2026-11-${day}T${hms}Z`;

// The old generation: it worked for days before its promotion was stamped (LATE). the operator's exchanges are real.
const OLD_LINES = [
  user('o1', d('01', '10:00:00'), `You are ${SEAT}-g3. Read /tmp/agent-init-${SEAT}-g3.md and follow all instructions in it.`),
  say('o2', d('01', '10:00:30'), 'Init read. Holding.'),
  user('o3', d('02', '09:00:00'), 'what is the deploy status?'),
  ...work('o4', d('02', '09:00:05'), 'Checking the deploy.'),
  say('o6', d('02', '09:00:20'), 'Deploy is green.'),
  user('o7', d('03', '11:00:00'), mail('lineage-daemon', '[WAL DELTA] hydrate from blue')),
  say('o8', d('03', '11:00:10'), 'Hydrate ingested.'),
  user('o9', d('04', '12:00:00'), mail('peer-dev', 'avatar release 6 is live')),
  say('o10', d('04', '12:00:10'), 'Acked peer-dev.'),
  user('o11', d('05', '14:00:00'), 'ship it'),
  say('o12', d('05', '14:00:05'), 'Shipped.'),
  user('o13', d('12', '08:00:00'), 'after the stamp: anything else?'),
  say('o14', d('12', '08:00:05'), 'Nothing pending.'),
];
// The green: init read, a hydrate, then the operator writes to it before its promotion and it answers; promote verify; then
// the conversation after the swap.
const GREEN_LINES = [
  user('g1', d('20', '07:54:50'), `You are ${SEAT}-g4. Read /tmp/agent-init-${SEAT}-g4.md and follow all instructions in it.`),
  ...work('g2', d('20', '07:54:52'), 'Reading my init file and the baton.'),
  say('g4', d('20', '07:55:32'), 'Context absorbed. Holding as the green.'),
  user('g5', d('20', '07:59:33'), mail('lineage-daemon', '[WAL DELTA] hydrate from blue')),
  say('g6', d('20', '08:00:16'), 'Hydrate ingested. Still holding.'),
  user('g7', d('20', '08:10:00'), 'green, are you up?'),
  ...work('g8', d('20', '08:10:02'), 'Checking my state.'),
  say('g10', d('20', '08:10:20'), 'Yes: up and holding; the blue answers you until the swap.'),
  user('g11', d('20', '08:20:00'), mail('peer-dev', 'heads-up for the green')),
  say('g12', d('20', '08:20:05'), 'Noted for after the swap.'),
  user('g13', d('20', '08:29:37'), mail('identity-store', '[PROMOTE] you are canonical')),
  say('g14', d('20', '08:31:20'), 'I am now canonical (gen 4). Promotion verified.'),
  user('g15', d('20', '08:55:00'), 'thanks'),
  say('g16', d('20', '08:55:01'), 'Anytime.'),
];
const greenFile = join(projects, `${GREEN}.jsonl`);
writeFileSync(join(projects, `${OLD}.jsonl`), OLD_LINES.join('\n') + '\n');
writeFileSync(greenFile, GREEN_LINES.join('\n') + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: GREEN, cwd: '/repo' }));

const { default: router, normalizeTranscript, liveFrom } = await import('./chat-transcript.js');
const { TranscriptTailer } = await import('../services/transcript-tail.js');

const lines = (items: any[]) => items.map((i: any) => i.uuid).filter((u: string, k: number, a: string[]) => k === 0 || a[k - 1] !== u);
// From the first person turn on, everything but the machine wakes: the agent mail after it stays (o9/o10, g11/g12).
const lines_ = (items: any[]) => lines(items);
const OLD_SHOWN = ['o3', 'o4', 'o4r', 'o6', 'o9', 'o10', 'o11', 'o12', 'o13', 'o14'];
// g13 is the promote wake, g14 its reply. The verdict says nothing was owed, so the turn stays (review v4 B-1) with its
// wake line empty and its reply NOT EMITTED (review v5 A-1): nothing of it can be drawn.
const GREEN_SHOWN = ['g7', 'g8', 'g8r', 'g10', 'g11', 'g12', 'g13', 'g15', 'g16'];
const CUT = ['o1', 'o2', 'o7', 'o8', 'g1', 'g2', 'g2r', 'g4', 'g5', 'g6', 'g14'];

async function withServer<T>(fn: (get: (q: string) => Promise<any>) => Promise<T>): Promise<T> {
  const express = (await import('express')).default;
  const a = express();
  a.use('/api/agents', router);
  const server = a.listen(0);
  try {
    const base = `http://127.0.0.1:${(server.address() as any).port}/api/agents/${SEAT}/transcript`;
    return await fn(async (q) => (await fetch(`${base}${q}`)).json());
  } finally {
    server.close();
  }
}

test('an operator message to a green before its promotion, and its answer, both stay; the machine hold phase goes', async () => {
  await withServer(async (get) => {
    const env = await get('');
    assert.deepEqual(lines(env.items), [...OLD_SHOWN, ...GREEN_SHOWN]);
    const texts = JSON.stringify(env.items);
    for (const words of ['Init read', 'Holding as the green', 'Hydrate ingested']) {
      assert.ok(!texts.includes(words), `"${words}" is a machine-only hold-phase turn: cut`);
    }
    assert.ok(!texts.includes('now canonical'), 'the promote reply is not emitted at all');
    const g13 = env.items.find((i: any) => i.uuid === 'g13');
    assert.deepEqual([g13.text, g13.wake, g13.owed], ['', 'promote', false]);
    assert.ok(!env.items.some((i: any) => i.uuid === 'g14'));
    assert.ok(texts.includes('green, are you up?') && texts.includes('the blue answers you until the swap'));
    assert.ok(texts.includes('Acked peer-dev') && texts.includes('Noted for after the swap'), 'after the takeover: kept');
  });
});

test('a late-promoted old generation keeps its real the operator exchanges, on every older page', async () => {
  await withServer(async (get) => {
    const whole = lines((await get('')).items);
    for (let limit = 1; limit <= 6; limit++) {
      const pages: any[] = [];
      let cursor = `${GREEN}~g16`;
      for (let n = 0; n < 60; n++) {
        const page = await get(`?before=${encodeURIComponent(cursor)}&limit=${limit}`);
        assert.equal(page.error, undefined, `limit ${limit}: ${page.error}`);
        pages.unshift(page);
        if (!page.has_older) break;
        cursor = page.oldest_id;
      }
      assert.equal(pages[0].has_older, false);
      const got = lines(pages.flatMap((p) => p.items));
      assert.deepEqual(got, whole.slice(0, -1), `limit ${limit}: no gap, no repeat`);
      for (const u of got) assert.ok(!CUT.includes(u), `limit ${limit}: ${u} paged in`);
      for (const u of ['o3', 'o6', 'o11', 'o12']) assert.ok(got.includes(u), `limit ${limit}: the operator's exchange ${u} kept`);
    }
  });
});

test('the live stream and the one-log builder keep the same pre-promotion exchange', () => {
  assert.deepEqual(lines(normalizeTranscript(GREEN_LINES, SEAT, GREEN, 500).items), GREEN_SHOWN);
  const tailer = new TranscriptTailer({
    agentId: SEAT, resolve: () => ({ path: greenFile, sid: GREEN }),
    interrupted: { read: () => [], sig: () => '' }, windowGone: () => false,
  });
  const events: any[] = [];
  const off = tailer.subscribe((ev) => events.push(ev));
  try {
    assert.equal(events[0].type, 'snapshot');
    assert.deepEqual(lines(events[0].items), GREEN_SHOWN);
  } finally {
    off();
  }
});

// ---- review follow-ups (its review) ---------------------------------------------------------------------

const T = (uuid: string, ts: string, role: string, text: string, extra: Record<string, unknown> = {}) =>
  ({ kind: 'text', role, text, uuid, ts, ...extra });
const job = (uuid: string, ts: string, id: string) => ({ kind: 'tool_use', role: 'assistant', tool: 'Bash', id, uuid, ts, input: {} });
const notice = (uuid: string, ts: string, id: string) =>
  T(uuid, ts, 'user', `<task-notification>\n<task-id>b${id}</task-id>\n<tool-use-id>${id}</tool-use-id>\n<status>completed</status>\n</task-notification>`);

test('a late-stamped generation (pm-seat-b g15): the operator\'s first turn is the takeover; his jobs\' notices stay', () => {
  const stamp = '2026-11-02T05:46:00.710085+00:00';
  const d2 = (hms: string) => `2026-11-02T${hms}Z`;
  const items = [
    T('s1', d2('01:20:00'), 'user', `You are pm-seat-b-g15. Read /tmp/agent-init-pm-seat-b-g15.md and follow all instructions in it.`),
    T('s2', d2('01:20:30'), 'assistant', 'Init read. Holding.'),
    T('s3', d2('01:31:29'), 'user', 'welp it promoted you so check if your predecessor finished'),
    job('s4', d2('01:31:40'), 'toolu_job1'),
    T('s5', d2('01:32:00'), 'assistant', 'Started the check in the background.'),
    notice('s6', d2('01:40:00'), 'toolu_job1'),
    T('s7', d2('01:40:05'), 'assistant', 'The predecessor finished: all four PRs merged.'),
    T('s8', d2('02:00:00'), 'user', mail('gm', 'status?')),
    T('s9', d2('02:00:05'), 'assistant', 'Replied to gm.'),
    T('s10', d2('04:57:53'), 'user', 'Analyze this recording.'),
    T('s11', d2('04:58:30'), 'assistant', 'Done.'),
    T('s12', d2('06:00:00'), 'user', 'after the stamp'),
  ];
  assert.deepEqual(liveFrom(items, stamp).map((i: any) => i.uuid), ['s3', 's4', 's5', 's6', 's7', 's8', 's9', 's10', 's11', 's12']);
  // Mail before any person turn is not a takeover: a green answering agent mail is still holding.
  const mailOnly = [items[0], items[1], items[7], items[8], items[11]];
  assert.deepEqual(liveFrom(mailOnly, stamp).map((i: any) => i.uuid), ['s12']);
  // Forwarded mail is not a person either.
  const fwd = T('f1', d2('01:25:00'), 'user', '[fwd from pm-seat-b-g14] [MSG from gm | medium] (sent 0m ago) hi — Read /tmp/agent-msg-m.md and act on it.');
  assert.deepEqual(liveFrom([items[0], items[1], fwd, items[11]], stamp).map((i: any) => i.uuid), ['s12']);
});

test('a job the hold phase launched: its notice as the first line after the promotion is still the hold phase\'s', () => {
  const items = [
    T('h1', d('20', '07:55:00'), 'user', `You are ${SEAT}-g4. Read /tmp/agent-init-${SEAT}-g4.md and go.`),
    job('h2', d('20', '07:56:00'), 'toolu_hold'),
    T('h3', d('20', '07:56:05'), 'assistant', 'Indexing the repo in the background while I hold.'),
    notice('n1', d('20', '08:40:00'), 'toolu_hold'),
    T('n2', d('20', '08:40:05'), 'assistant', 'Index done.'),
    T('p1', d('20', '08:50:00'), 'user', 'hi'),
    job('p2', d('20', '08:50:05'), 'toolu_live'),
    T('p3', d('20', '08:50:10'), 'assistant', 'Running it.'),
    notice('p4', d('20', '09:10:00'), 'toolu_live'),
    T('p5', d('20', '09:10:05'), 'assistant', 'Finished.'),
  ];
  assert.deepEqual(liveFrom(items, SWAP).map((i: any) => i.uuid), ['p1', 'p2', 'p3', 'p4', 'p5']);
  // A notice for a job started after the promotion opens the chat.
  assert.deepEqual(liveFrom([...items.slice(0, 3), job('q1', d('20', '08:35:00'), 'toolu_after'), notice('q2', d('20', '08:40:00'), 'toolu_after'),
    T('q3', d('20', '08:40:05'), 'assistant', 'Done.')], SWAP).map((i: any) => i.uuid), ['q2', 'q3']);
});

test('a rotated log whose first input is the operator\'s long message (marked system on its length) keeps it', () => {
  const long = `Here is everything from the call. ${'x'.repeat(900)}`;
  const lines = [user('r1', d('20', '09:00:00'), long), say('r2', d('20', '09:00:05'), 'Got it, on it.')];
  const env = normalizeTranscript(lines, SEAT, GREEN, 500);
  assert.deepEqual(lines_(env.items), ['r1', 'r2']);
  assert.equal(env.items[0].is_system, true, 'still drawn as before');
  // A brief ("You are ...") in the same place is still the harness's.
  const brief = [user('r3', d('20', '09:00:00'), `You are ${SEAT}. ${'y'.repeat(900)}`), say('r4', d('20', '09:00:05'), 'Brief read.'),
    user('r5', d('20', '09:01:00'), 'hello')];
  assert.deepEqual(lines_(normalizeTranscript(brief, SEAT, GREEN, 500).items), ['r5']);
  // Before the promotion a length-only mark stands: a green's long brief is the likelier reading there.
  const early = [user('r6', d('20', '07:00:00'), long), say('r7', d('20', '07:00:05'), 'Holding.'), user('r8', d('20', '09:00:00'), 'hello')];
  assert.deepEqual(lines_(normalizeTranscript(early, SEAT, GREEN, 500).items), ['r8']);
});

test('a paging cursor from before the head cut is unknown, never the wrong items; the versioned one works', async () => {
  await withServer(async (get) => {
    const old = await get(`?before=${encodeURIComponent(`${GREEN}~#3`)}&limit=2`);
    assert.equal(old.error, 'unknown cursor');
    const now = await get(`?before=${encodeURIComponent(`${GREEN}~#h1.3`)}&limit=2`);
    assert.equal(now.error, undefined);
    assert.ok(now.items.length > 0);
  });
});
