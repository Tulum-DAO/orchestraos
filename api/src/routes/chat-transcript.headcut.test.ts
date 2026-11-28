/**
 * The stitched chat is one conversation (the operator: "as if the user never knows it's there"). A blue-green
 * green lives 30-60 minutes before its promotion: it reads its init file, takes in hydrate digests and answers them,
 * verifies its promote notice and writes an "I am now canonical" recap. None of that is the conversation. After a
 * swap the chat reads the blue's last turn, then the green's first turn after its promotion that is not a machine
 * wake, nothing between: on the poll route, on every older page, and on the live stream. The divider between two
 * generations is there only when an operator asks for it (?dividers=1).
 *
 * Run: npx tsx --test src/routes/chat-transcript.headcut.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, appendFileSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const SEAT = 'zz-headcut-seat';                 // never a real tmux session on any box
const BLUE = '55555555-2222-4333-8444-000000000005';
const GREEN = '55555555-2222-4333-8444-000000000006';
const SWAP = '2026-11-20T08:29:10.973388+00:00'; // the blue retired, the green promoted (one stamp)

const root = testTmpDir('transcript-headcut-');
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
  g.run(1, SEAT, 5, BLUE, '2026-11-19T22:43:20.010087+00:00', SWAP);
  g.run(2, SEAT, 6, GREEN, SWAP, null);
  db.prepare("INSERT INTO canonical VALUES (?, 2, ?, 'online')").run(SEAT, SEAT);
  db.close();
}

/** identity_store/retire_notice.py's exact text: its time is strftime("%Y-%m-%d %H:%M:%SZ"), with a SPACE. */
const retire = (ts: string, swap: string, root: string, gen: number) => `[ROTATION] You were RETIRED at ${ts} (swap ${swap}): ${root} generation ${gen} is now canonical and already holds your digest. Stop here. Commit, push or send nothing further; your mail now goes to the successor. Nothing to answer.`;

const at = (hms: string) => `2026-11-20T${hms}Z`;
const user = (uuid: string, hms: string, text: string, extra: Record<string, unknown> = {}) =>
  JSON.stringify({ type: 'user', uuid, timestamp: at(hms), message: { role: 'user', content: text }, ...extra });
const say = (uuid: string, hms: string, text: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: at(hms),
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] },
});
/** One reply line that says something and runs a tool, and the tool's result line. */
const work = (uuid: string, hms: string, text: string): string[] => [
  JSON.stringify({
    type: 'assistant', uuid, timestamp: at(hms),
    message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [
      { type: 'text', text }, { type: 'tool_use', id: `t-${uuid}`, name: 'Bash', input: { command: 'true' } }] },
  }),
  JSON.stringify({ type: 'user', uuid: `${uuid}r`, timestamp: at(hms), message: { role: 'user', content: [
    { type: 'tool_result', tool_use_id: `t-${uuid}`, content: 'ok' }] } }),
];
const queued = (uuid: string, hms: string, prompt: string) => JSON.stringify({
  type: 'attachment', uuid, timestamp: at(hms), attachment: { type: 'queued_command', prompt, origin: { kind: 'human' } },
});
const mail = (from: string, body: string) => `[MSG from ${from} | high] (sent 0m ago) ${body} — Read /tmp/agent-msg-x.md and act on it.`;

// The blue: its last turn with the operator, then the retire notice typed in after the swap, and its answer.
const BLUE_LINES = [
  user('b1', '07:00:00', 'can you check the deploy?'),
  ...work('b2', '07:00:05', 'Checking.'),
  say('b4', '07:00:10', 'Deploy is green.'),
  user('b5', '08:29:11', retire('2026-11-20 08:29:10Z', '520', SEAT, 6)),
  say('b6', '08:29:12', 'Acknowledged, retiring.'),
];
// The green: its hold phase (boot line + init read, two hydrates and its answers, the promote notice verified after a
// stop hook, the recap), a background fire, then the operator.
const GREEN_LINES = [
  user('g1', '07:54:50', `You are ${SEAT}-g6. Read /tmp/agent-init-${SEAT}-g6.md and follow all instructions in it.`),
  ...work('g2', '07:54:52', 'Reading my init file first.'),
  say('g4', '07:55:32', 'Context absorbed. Holding as the green.'),
  user('g5', '07:59:33', mail('lineage-daemon', '[WAL DELTA] hydrate from blue')),
  say('g6', '08:00:16', 'Hydrate ingested. Still holding.'),
  user('g7', '08:29:37', mail('lineage-daemon', '[WAL DELTA] hydrate from blue')),   // lands after the swap
  say('g8', '08:29:57', 'Second hydrate ingested.'),
  user('g9', '08:29:57', 'Stop hook feedback:\n[QUEUE-DIGEST] 1 pending message(s) older than 30s', { isMeta: true }),
  ...work('g10', '08:29:58', 'A promote notice. Verifying against the DB.'),
  say('g12', '08:31:20', 'I am now canonical (gen 6). Promotion verified.'),
  user('g13', '08:32:37', mail('b-fire-watcher', 'BG FIRE — zz-headcut-seat -> DRAINED')),
  ...work('g14', '08:32:41', 'Fire notice for swap 520, acking.'),
  queued('g16', '08:32:44', 'why do I see the hold phase?'),
  ...work('g17', '08:32:45', 'Reading your message.'),
  say('g19', '08:34:49', 'You are right; fixing it.'),
  user('g20', '08:39:38', mail('peer-dev', 'avatar 3D body: the operator sees no change')),
  say('g21', '08:40:27', 'The avatar is live.'),
  user('g22', '08:45:00', mail('fleet-beat-bg', '[BG-PAGE] canvas-dev: bg-human-hold')),
  say('g23', '08:45:10', 'Paged canvas-dev.'),
  user('g24', '08:50:00', mail('lineage-daemon', '[WAL DELTA] stray hydrate')),
  say('g25', '08:50:05', 'Stray digest, nothing to do.'),
  user('g26', '08:55:00', 'thanks'),
  say('g27', '08:55:01', 'Anytime.'),
];
const greenFile = join(projects, `${GREEN}.jsonl`);
writeFileSync(join(projects, `${BLUE}.jsonl`), BLUE_LINES.join('\n') + '\n');
writeFileSync(greenFile, GREEN_LINES.join('\n') + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: GREEN, cwd: '/repo' }));

const { default: router, normalizeTranscript, liveFrom, hideWakes, dropWakeBatchEntries } = await import('./chat-transcript.js');
const { wakeKind } = await import('../lib/audience.js');
const { TranscriptTailer } = await import('../services/transcript-tail.js');

/** The chat by log line: each line's uuid once, in order (a reply that runs a tool is two items of one line). */
const lines = (items: any[]) => items.map((i: any) => i.uuid).filter((u: string, k: number, a: string[]) => k === 0 || a[k - 1] !== u);
const BLUE_SHOWN = ['b1', 'b2', 'b2r', 'b4'];
const GREEN_SHOWN = ['g16', 'g17', 'g17r', 'g19', 'g20', 'g21', 'g22', 'g23', 'g26', 'g27'];
const HOLD = ['g1', 'g2', 'g2r', 'g4', 'g5', 'g6', 'g7', 'g8', 'g10', 'g10r', 'g12', 'g13', 'g14', 'g14r', 'g24', 'g25', 'b5', 'b6'];

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

test('after a swap: the blue\'s last turn, then the green\'s first turn after its promotion, nothing between', async () => {
  await withServer(async (get) => {
    const env = await get('');
    assert.deepEqual(lines(env.items), [...BLUE_SHOWN, ...GREEN_SHOWN]);
    const texts = JSON.stringify(env.items) + JSON.stringify(env.render_items);
    for (const words of ['init file', 'Holding as the green', 'Hydrate ingested', 'Second hydrate', 'promote notice',
      'now canonical', 'RETIRED', 'retiring', 'Fire notice', 'WAL DELTA', 'Stray digest', 'BG-PAGE', 'BG FIRE']) {
      assert.ok(!texts.includes(words), `"${words}" is the hold phase or a wake: never in the chat`);
    }
    assert.ok(!env.items.some((i: any) => i.kind === 'rotation'), 'no divider by default');
    assert.ok(!env.render_items.some((n: any) => n.kind === 'rotation'));
    // A beat wake keeps its place as an empty, flagged opener (the page it started stays); nothing draws it.
    const page = env.items.find((i: any) => i.uuid === 'g22');
    assert.deepEqual([page.text, page.wake], ['', 'beat']);
    assert.ok(!env.render_items.some((n: any) => n.kind === 'user' && n.key?.startsWith('g22')));
    // The operator's message mid-fire is his: it is the green's first line.
    assert.equal(env.items.find((i: any) => i.uuid === 'g16').text, 'why do I see the hold phase?');
  });
});

test('the divider is there for an operator who asks (?dividers=1), between the same two lines', async () => {
  await withServer(async (get) => {
    for (const flag of ['1', 'true']) {
      const env = await get(`?dividers=${flag}`);
      assert.deepEqual(lines(env.items), [...BLUE_SHOWN, `rot:${GREEN}`, ...GREEN_SHOWN]);
      const div = env.items.find((i: any) => i.kind === 'rotation');
      assert.deepEqual([div.from_generation, div.to_generation, div.ts], [5, 6, SWAP]);
      assert.equal(env.render_items.filter((n: any) => n.kind === 'rotation').length, 1);
    }
    assert.ok(!(await get('?dividers=0')).items.some((i: any) => i.kind === 'rotation'));
  });
});

test('older pages never reveal a hold phase or a wake, at every page size, with and without the divider', async () => {
  await withServer(async (get) => {
    for (const dividers of [false, true]) {
      const flag = dividers ? '&dividers=1' : '';
      const whole = lines((await get(dividers ? '?dividers=1' : '')).items);
      for (let limit = 1; limit <= 8; limit++) {
        const pages: any[] = [];
        let cursor = `${GREEN}~g27`;
        for (let n = 0; n < 60; n++) {
          const page = await get(`?before=${encodeURIComponent(cursor)}&limit=${limit}${flag}`);
          assert.equal(page.error, undefined, `limit ${limit}: ${page.error}`);
          pages.unshift(page);
          if (!page.has_older) break;
          cursor = page.oldest_id;
        }
        assert.equal(pages[0].has_older, false, 'the walk reaches the start');
        assert.deepEqual(lines(pages.flatMap((p) => p.items)), whole.slice(0, -1), `limit ${limit}: no gap, no repeat`);
        for (const p of pages) for (const it of p.items) assert.ok(!HOLD.includes(it.uuid), `limit ${limit}: ${it.uuid} paged in`);
      }
    }
  });
});

test('the live stream starts the green at the same line, and a later wake is never sent', () => {
  const tailer = new TranscriptTailer({
    agentId: SEAT, resolve: () => ({ path: greenFile, sid: GREEN }),
    interrupted: { read: () => [], sig: () => '' }, windowGone: () => false,
  });
  const events: any[] = [];
  const off = tailer.subscribe((ev) => events.push(ev));
  try {
    assert.equal(events[0].type, 'snapshot');
    assert.deepEqual(lines(events[0].items), GREEN_SHOWN);
    assert.ok(!events[0].render_items.some((n: any) => n.kind === 'rotation'));
    // A stray hydrate and its answer land: nothing to send.
    appendFileSync(greenFile, [user('g28', '09:00:00', mail('lineage-daemon', '[WAL DELTA] late hydrate')),
      say('g29', '09:00:05', 'Late digest, nothing to do.')].join('\n') + '\n');
    tailer.tick();
    assert.equal(events.length, 1, 'a rotation wake and its answer are never streamed');
    // The operator writes: exactly his turn goes out, as a delta.
    appendFileSync(greenFile, [user('g30', '09:05:00', 'one more thing'), say('g31', '09:05:01', 'Sure.')].join('\n') + '\n');
    tailer.tick();
    assert.equal(events.length, 2);
    assert.equal(events[1].type, 'delta');
    assert.deepEqual(lines(events[1].items), ['g30', 'g31']);
  } finally {
    off();
    writeFileSync(greenFile, GREEN_LINES.join('\n') + '\n');
  }
});

test('the one-log builder (stream, export) cuts where the poll route does', async () => {
  const env = normalizeTranscript(GREEN_LINES, SEAT, GREEN, 500);
  assert.deepEqual(lines(env.items), GREEN_SHOWN);
  await withServer(async (get) => {
    const two = await get('?limit=3');
    assert.deepEqual(two.items.map(({ ref: _r, ...it }: any) => it), normalizeTranscript(GREEN_LINES, SEAT, GREEN, 3, false, true).items);
  });
  // Another seat reading the same file never gets this seat's promotion (and so no cut).
  assert.deepEqual(lines(normalizeTranscript(GREEN_LINES, 'zz-other-seat', GREEN, 500).items)[0], 'g1');
});

test('liveFrom: no promotion time, or no times at all, leaves the log whole; promotion with no turn yet shows nothing', () => {
  const items = [
    { kind: 'text', role: 'user', text: 'hi', uuid: 'u1', ts: at('07:00:00') },
    { kind: 'text', role: 'assistant', text: 'hello', uuid: 'a1', ts: at('07:00:01') },
  ];
  assert.equal(liveFrom(items, null), items);
  assert.equal(liveFrom(items, 'not a time'), items);
  const untimed = items.map(({ ts: _t, ...it }) => it);
  assert.equal(liveFrom(untimed, SWAP), untimed, 'no evidence of when: nothing is cut');
  // Before the promotion a person's exchange stays (the cap); the machine-only hold phase goes.
  assert.deepEqual(liveFrom(items, SWAP), items, 'an operator\'s exchange before the promotion stays');
  const hold = [
    { kind: 'text', role: 'user', text: `You are ${SEAT}-g6. Read /tmp/agent-init-${SEAT}-g6.md and go.`, uuid: 'h1', ts: at('07:00:00') },
    { kind: 'text', role: 'assistant', text: 'reading my init', uuid: 'h2', ts: at('07:00:01') },
    { kind: 'text', role: 'user', text: mail('peer-dev', 'hello green'), uuid: 'h3', ts: at('07:10:00') },
    { kind: 'text', role: 'assistant', text: 'noted', uuid: 'h4', ts: at('07:10:01') },
  ];
  assert.deepEqual(liveFrom(hold, SWAP), [], 'machine-only hold phase, no turn after: nothing to show yet');
  assert.deepEqual(liveFrom(items, '2026-11-20T06:00:00Z'), items, 'all of it after: whole');
  // A machine turn straddling the promotion is the hold phase's: the cut is at the next turn.
  const late = [{ kind: 'text', role: 'assistant', text: 'late', uuid: 'a2', ts: at('09:00:00') },
    { kind: 'text', role: 'user', text: 'next', uuid: 'u2', ts: at('09:01:00') }];
  assert.deepEqual(liveFrom([...hold, ...late], SWAP).map((i: any) => i.uuid), ['u2']);
  // A person's turn before it is the takeover (its review): the init read before it goes, everything
  // after it stays (machine wakes are already out: hideWakes), the mail turn included.
  assert.deepEqual(liveFrom([...hold.slice(0, 2), ...items, ...hold.slice(2), ...late], SWAP).map((i: any) => i.uuid),
    ['u1', 'a1', 'h3', 'h4', 'a2', 'u2']);
  assert.deepEqual(liveFrom([...hold, ...items, ...late], SWAP).map((i: any) => i.uuid), ['u1', 'a1', 'a2', 'u2']);
});

test('wakeKind: each emitter\'s full format is a wake; the same words typed or pasted by a person are not', () => {
  const fwd = '[fwd from gm-g3] [MSG from lineage-daemon | high] [SENT 3190m AGO — world may have moved since; verify current state before acting on this] [WAL DELTA] hydrate from gm blue — Read /tmp/agent-msg-msg_1.md and act on it.';
  const cases: [string, string | null][] = [
    [mail('lineage-daemon', '[WAL DELTA] hydrate from gm blue'), 'rotation'],
    [mail('somebody', '[WAL DELTA] hydrate from gm blue'), 'rotation'],
    [mail('bg-hydrate', '[WAL DELTA] seed'), 'rotation'],
    ['[MSG from identity-store | high] [SENT 13m AGO — world may have moved since; verify current state before acting on this] [PROMOTE] you are canonical — verify against the DB, do not take this on trust — Read /tmp/agent-msg-m.md and act on it.', 'rotation'],
    [mail('somebody', '[PROMOTE] you are canonical — verify against the DB'), 'rotation'],
    [fwd, 'rotation'],
    [mail('gm-g125', 'From the retired blue: three session wakes cancelled, yours to recreate'), 'rotation'],
    [retire('2026-11-03 00:18:30Z', '468', 'pm-seat-b', 16), 'rotation'],            // the emitter's own format
    [retire('2026-11-03T00:18:30Z', '468', 'pm-seat-b', 16), 'rotation'],            // an ISO time too
    [retire(SWAP, '520', 'gm', 130), 'rotation'],
    // the operator quoting a retire notice inside his own message, or asking about one: his.
    [`why did gm get this? ${retire('2026-11-03 00:18:30Z', '468', 'gm', 130)}`, null],
    ['[ROTATION] You were RETIRED at 2026-11-03 00:18:30Z -- what does this mean?', null],
    ["ingest your hydrate digest (your predecessor's handoff + delivered context), then hold", 'rotation'],
    ['[LINEAGE INIT] You are the successor to gm-g129. Read the handoff', 'rotation'],
    ['[LINEAGE RESUME] you are canonical — start the lineage work now', 'rotation'],
    [mail('b-fire-watcher', 'BG FIRE — gm -> DRAINED'), 'beat'],
    ['[MSG from b-fire-watcher | medium] [SENT 12m AGO — world may have moved since] BG FIRE: x -> DRAINED (first autonomous rotation underway) — Read /tmp/agent-msg-m.md and act on it.', 'beat'],
    [mail('fleet-beat-bg', '[BG-PAGE] canvas-dev: bg-human-hold'), 'beat'],
    [mail('somebody', '[BG-PAGE] gm: green-wake-failed'), 'beat'],
    ['bg-beat -> gm: you will be rotating. Your context is 10% past the rotation line', 'beat'],
    ['Stop hook feedback:\n[QUEUE-DIGEST] 1 pending message(s) older than 30s', 'beat'],
    ['[QUEUE-DIGEST] You have 3 pending message(s) in your mailbox:', 'beat'],
    // A person's words, however they start: never a wake.
    ['[WAL DELTA] hydrate from gm blue — why do I still see this in the chat?', null],
    ['[WAL DELTA]', null],
    ['[PROMOTE] what does this notice do?', null],
    ['[ROTATION] what is swap 520?', null],
    ['[LINEAGE] spawn your green', null],
    ['BG FIRE keeps showing in my chat, fix it', null],
    ['bg-beat is noisy today', null],
    ['[BG-PAGE] looks wrong', null],
    ['[QUEUE-DIGEST] what is this?', null],
    // Hook output at the start of a line is the harness, whatever it says; quoted later, his.
    ['Stop hook feedback: Self-audit: your last turn asked the operator to approve/decide something without submitting an approval.', 'beat'],
    ['Stop hook feedback:\nanything a hook prints', 'beat'],
    ['why do I see "Stop hook feedback: Self-audit: ..." in the chat?', null],
    ['[MSG from lineage-daemon | high] (sent 0m ago) [WAL DELTA] hydrate from gm blue', null],   // pasted, no envelope tail
    [mail('peer-dev', 'avatar 3D body: the operator sees no change'), null],
    [mail('orchestra-builder', 're: Review only: MERGE-OK'), null],
    [mail('gm-g125', 'the retired blue says hi'), null],
    ['why do I see the hold phase?', null],
    ['please explain the [WAL DELTA] hydrate to me', null],
    ['[DECISION ANSWERED dec_1] yes', null],
  ];
  for (const [text, want] of cases) assert.equal(wakeKind(text), want, text);
});

test('hideWakes: a rotation wake\'s turn goes whole; a beat\'s line is emptied; a queued wake takes only its own line', () => {
  const t = (uuid: string, role: string, text: string, extra: Record<string, unknown> = {}) => ({ kind: 'text', role, text, uuid, ...extra });
  const out = hideWakes([
    t('u1', 'user', 'do the thing'),
    t('a1', 'assistant', 'working'),
    t('q1', 'user', mail('lineage-daemon', '[WAL DELTA] hydrate'), { queued: true }),
    t('a2', 'assistant', 'done with the thing'),
    t('w1', 'user', mail('lineage-daemon', '[WAL DELTA] hydrate')),
    t('a3', 'assistant', 'hydrate ingested'),
    t('w2', 'user', mail('fleet-beat-bg', '[BG-PAGE] x: hold')),
    t('a4', 'assistant', 'paged x'),
    t('p1', 'user', '[WAL DELTA] hydrate from gm blue — why do I see this?'),
    t('a5', 'assistant', 'It is the hydrate digest; it should be hidden.'),
    t('p2', 'user', 'BG FIRE — gm -> DRAINED showed up, what is it?'),
    t('a6', 'assistant', 'A fire notice.'),
    t('h1', 'user', 'Stop hook feedback: Self-audit: your last turn asked the operator to decide without a card.'),
    t('a7', 'assistant', 'Card filed.'),
    t('p3', 'user', 'I saw "Stop hook feedback: Self-audit" in my chat'),
    t('a8', 'assistant', 'That is hidden now.'),
  ]);
  assert.deepEqual(out.map((i: any) => [i.uuid, i.text, i.wake ?? null]), [
    ['u1', 'do the thing', null], ['a1', 'working', null], ['a2', 'done with the thing', null],
    ['w2', '', 'beat'], ['a4', 'paged x', null],
    ['p1', '[WAL DELTA] hydrate from gm blue — why do I see this?', null], ['a5', 'It is the hydrate digest; it should be hidden.', null],
    ['p2', 'BG FIRE — gm -> DRAINED showed up, what is it?', null], ['a6', 'A fire notice.', null],
    ['h1', '', 'beat'], ['a7', 'Card filed.', null],
    ['p3', 'I saw "Stop hook feedback: Self-audit" in my chat', null], ['a8', 'That is hidden now.', null]]);
});

test('a processed batch drops its machine wakes, and a batch of only wakes is dropped', () => {
  const batch = (entries: string[]) => ({ kind: 'queued_batch', count: entries.length, entries: entries.map((body) => ({ agent: 'x', sent_ts: '', body })) });
  const out = dropWakeBatchEntries([
    batch([mail('fleet-beat-bg', '[BG-PAGE] y: hold'), mail('peer-dev', 'hello')]),
    batch([mail('lineage-daemon', '[WAL DELTA] z')]),
    { kind: 'text', role: 'user', text: 'hi' },
    // The store's own rows: the sender and the raw body, no envelope (services/queued-merge.ts).
    { kind: 'queued_batch', count: 2, entries: [{ agent: 'lineage-daemon', sent_ts: '', body: 'hydrate digest: 3 items' },
      { agent: 'gm', sent_ts: '', body: 'the real note' }] },
  ]);
  assert.equal(out.length, 3);
  assert.deepEqual(out[2].entries.map((e: any) => e.body), ['the real note']);
  assert.deepEqual(out[0].entries.map((e: any) => e.body), [mail('peer-dev', 'hello')]);
  assert.equal(out[0].count, 1);
});
