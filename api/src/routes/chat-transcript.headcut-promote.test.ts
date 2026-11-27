/**
 * The promote wake's turn in the stitched chat (orchestraos-builder rulings 2026-11-21; review v2:
 * ONE source of "owed"). The first turn after a generation's promotion whose only input is the promote wake is left
 * out, its reply included ("I am now canonical, I checked the registry ..."), ONLY when the capsule's verdict for that
 * generation says nothing was owed (<data dir>/state/agent-handoffs/owed/<root>.<generation id>.json, owed:false,
 * written by conversation_capsule.py at the promote wake). owed:true keeps the turn and draws only its FINAL reply (no
 * tool call after it). No verdict, or one that cannot be read or is another generation's: kept (positive evidence
 * only). A turn that also holds an operator message stays. The API never reads the previous generation's log for this. The wake reaches a seat in two shapes: (a) router-injected
 * "[MSG from identity-store | prio] ... [PROMOTE] you are canonical ... — Read /tmp/agent-msg-ID.md and act on it."
 * and (b) the Stop hook's one-row [QUEUE-DIGEST] (an isMeta line). Real fixtures: swaps 526 and 527 (fixtures/
 * headcut-promote, trimmed; README there). Shape (a) is synthesized on the same logs with the router's real line.
 *
 * Run: npx tsx --test src/routes/chat-transcript.headcut-promote.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const root = testTmpDir('transcript-headcut-promote-');
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
process.env.MSG_DB_PATH = join(orch, 'no-tasks.db');          // no msg_store: nothing merged, nothing written
process.env.APPROVAL_DB_PATH = join(orch, 'no-approvals.db');
process.env.NTFY_TOKEN_FILE = join(orch, 'no-such-ntfy-token');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}

test('FENCE: every store this suite could reach is a scratch path (asserted before the routes load)', () => {
  for (const k of ['HOME', 'ORCHESTRA_DIR', 'MSG_DB_PATH', 'APPROVAL_DB_PATH', 'NTFY_TOKEN_FILE']) {
    const v = String(process.env[k] ?? '');
    assert.ok(v.startsWith(root), `${k}=${v} is not under the suite's scratch dir`);
    assert.ok(!/\/\.orchestra\/state|agent-orchestra\/state|orchestraos\/state/.test(v), `${k}=${v}`);
  }
});

const FIX = new URL('./fixtures/headcut-promote/', import.meta.url).pathname;
const fixture = (name: string): string[] => readFileSync(join(FIX, name), 'utf-8').split('\n').filter(Boolean);
const P527 = '2026-11-21T21:17:13.402253+00:00';
const P526 = '2026-11-21T15:28:50.052846+00:00';
/** message-router.py format_injection's exact line for the promote notice (shape a; priority 'critical' since
 *  promote_notice.default_notifier sends it so). */
const ROUTER_WAKE = (id: string) => `[MSG from identity-store | critical] (sent 0m ago) [PROMOTE] you are canonical — verify against the DB, do not take this on trust — Read /tmp/agent-msg-${id}.md and act on it. To reply: python3 /opt/orchestra/orchestraos/msg_store.py send --from YOUR_AGENT_ID --to identity-store --type reply --subject 're: [PROMOTE] you are canonical — verify aga' --body 'your reply'`;
const DIGEST_527 = 'Stop hook feedback:\n[QUEUE-DIGEST] 1 pending message(s) for orchestraos-builder older than 30s — batch-process now (read each via python3 msg_store.py get --id <id>, ack after processing via python3 msg_store.py ack --id <id>, stale-banner discipline applies):\n  msg_f0000035_00000055 | identity-store | [PROMOTE] you are canonical — verify against the DB, do not  — python3 msg_store.py get --id msg_f0000035_00000055 ; python3 msg_store.py ack --id msg_f0000035_00000055';
const DIGEST_529 = 'Stop hook feedback:\n[QUEUE-DIGEST] 1 pending message(s) for peer-dev older than 30s — batch-process now (read each via store, ack after processing, stale-banner discipline applies):\n  msg_f0000038_00000056 | identity-store | [PROMOTE] you are canonical — verify against the DB, do not ';

/** The green log with its Stop-hook digest (shape b) swapped for the router's line (shape a), same row and time. */
function asRouterShape(lines: string[]): string[] {
  let n = 0;
  const out = lines.map((l) => {
    const o = JSON.parse(l);
    const c = o?.message?.content;
    if (o.type === 'user' && o.isMeta && typeof c === 'string' && c.includes('| identity-store | [PROMOTE]')) {
      n++;
      delete o.isMeta;
      o.message.content = ROUTER_WAKE(/msg_\w+/.exec(c)![0]);
      return JSON.stringify(o);
    }
    return l;
  });
  assert.equal(n, 1, 'exactly one promote digest in the fixture');
  return out;
}
const at = (iso: string, plus: number) => new Date(Date.parse(iso) + plus * 1000).toISOString();
let gid = 0;
const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
db.exec(`CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
           always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
         CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
           conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
           promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
         CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
/** One seat: a blue (null = its file is not on this box) and the green promoted at `promoted`. */
function seat(name: string, n: number, blue: string[] | null, green: string[], promoted: string,
              verdict: boolean | 'garbage' | 'malformed' | 'other-generation' | 'other-root' | 'other-promotion' | 'no-promoted-at' | null = null) {
  const B = `66666666-2222-4333-8444-0000000000${String(2 * n).padStart(2, '0')}`;
  const G = `66666666-2222-4333-8444-0000000000${String(2 * n + 1).padStart(2, '0')}`;
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run(name, '/repo');
  const g = db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at, retired_at) VALUES (?,?,?,?,?,?)');
  g.run(++gid, name, 51, B, '2026-11-21T05:00:00+00:00', promoted);
  g.run(++gid, name, 52, G, promoted, null);
  db.prepare("INSERT INTO canonical VALUES (?, ?, ?, 'online')").run(name, gid, name);
  if (blue) writeFileSync(join(projects, `${B}.jsonl`), blue.join('\n') + '\n');
  writeFileSync(join(projects, `${G}.jsonl`), green.join('\n') + '\n');
  writeFileSync(join(orch, 'state', 'agents', `${name}.json`), JSON.stringify({ session_id: G, cwd: '/repo' }));
  if (verdict !== null) {
    const dir = join(orch, 'state', 'agent-handoffs', 'owed');
    mkdirSync(dir, { recursive: true });
    const body = verdict === 'garbage' ? '{"owed": tru'
      : verdict === 'malformed' ? JSON.stringify({ schema: 'owed-verdict/1', root: name, generation_id: gid, owed: 0 })
      : verdict === 'other-root' ? JSON.stringify({ schema: 'owed-verdict/1', root: `${name}-x`, generation_id: gid, owed: false })
      : verdict === 'other-promotion' ? JSON.stringify({ schema: 'owed-verdict/1', root: name, generation_id: gid, owed: false,
        promoted_at: '2026-09-01T00:00:00+00:00' })
      : verdict === 'no-promoted-at' ? JSON.stringify({ schema: 'owed-verdict/1', root: name, generation_id: gid, owed: false,
        promoted_at: null })
      : JSON.stringify({ schema: 'owed-verdict/1', root: name, generation_id: verdict === 'other-generation' ? gid + 100 : gid,
        promoted_at: promoted, owed: verdict === 'other-generation' ? false : verdict, reasons: [] });
    writeFileSync(join(dir, `${name}.${gid}.json`), body);
  }
  return { name, green: G, greenId: gid, lines: green, promoted };
}

const B527 = fixture('swap527-blue-g51.jsonl'), G527 = fixture('swap527-green-g52.jsonl');
const B526 = fixture('swap526-blue-g50.jsonl'), G526 = fixture('swap526-green-g51.jsonl');
const B512 = fixture('swap512-blue-g1.jsonl'), G512 = fixture('swap512-green-g2.jsonl');
const P512 = '2026-11-19T02:01:36.223206+00:00', WAKE512 = '2026-11-19T02:35:21.000Z';
const WAKE527 = '2026-11-21T21:17:55.000Z', WAKE526 = '2026-11-21T15:29:23.000Z';
// An operator message typed while the promote turn ran (a queued_command), 20 s into it.
const withQueued = (lines: string[], wakeTs: string) => {
  const k = lines.findIndex((l) => JSON.parse(l).timestamp > at(wakeTs, 20));
  const q = JSON.stringify({ type: 'attachment', uuid: 'op-queued', timestamp: at(wakeTs, 20),
    attachment: { type: 'queued_command', prompt: 'also, is the deploy done?', origin: { kind: 'human' } } });
  return [...lines.slice(0, k), q, ...lines.slice(k)];
};
// The router's wake with the operator's own words typed after it in the same prompt.
const withTail = (lines: string[]) => lines.map((l) => (l.includes('[MSG from identity-store | critical]')
  ? l.replace(`--body 'your reply'"`, `--body 'your reply' is the deploy done?"`) : l));
// A decision-card answer relayed while the promote turn ran (review v3 F3), 20 s into it.
const withCard = (lines: string[], wakeTs: string) => {
  const k = lines.findIndex((l) => JSON.parse(l).timestamp > at(wakeTs, 20));
  const q = JSON.stringify({ type: 'attachment', uuid: 'op-card', timestamp: at(wakeTs, 20),
    attachment: { type: 'queued_command', origin: { kind: 'human' },
      prompt: "[DECISION ANSWERED apr_0000aaaa_1] 'Ship it?' -> Yes. Act on it, then run: python3 approvals.py ack" } });
  return [...lines.slice(0, k), q, ...lines.slice(k)];
};
// review v4 T-1: the operator types into the promote turn and the seat answers him in it.
const withAnswer = (lines: string[], wakeTs: string) => {
  const k = lines.findIndex((l) => JSON.parse(l).timestamp > at(wakeTs, 20));
  const q = JSON.stringify({ type: 'attachment', uuid: 'op-asks', timestamp: at(wakeTs, 20),
    attachment: { type: 'queued_command', prompt: 'is the deploy done?', origin: { kind: 'human' } } });
  const a = JSON.stringify({ type: 'assistant', uuid: 'op-answer', timestamp: at(wakeTs, 21),
    message: { id: 'm-op-answer', role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text: 'Yes: the deploy is done.' }] } });
  const t = JSON.stringify({ type: 'assistant', uuid: 'op-work', timestamp: at(wakeTs, 22),
    message: { id: 'm-op-work', role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'tool_use', id: 't-op-work', name: 'Bash', input: { command: 'true' } }] } });
  return [...lines.slice(0, k), q, a, t, ...lines.slice(k)];
};
// review v3 F2: the owed reply, then a Stop-hook digest (isMeta in today's CLI, a plain line in older ones), then the
// seat acks gm's mail. Shape a wake, 40 s after the 527 promotion.
const hookGreen = (meta: boolean): string[] => {
  const T = (s: number) => at(P527, s);
  const u = (uuid: string, s: number, content: any, extra: Record<string, unknown> = {}) =>
    JSON.stringify({ type: 'user', uuid, timestamp: T(s), message: { role: 'user', content }, ...extra });
  const a = (uuid: string, s: number, content: any[]) =>
    JSON.stringify({ type: 'assistant', uuid, timestamp: T(s), message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content } });
  return [
    u('h0', -3000, 'You are zz-promote-hook-g52. Read /tmp/agent-init-zz-promote-hook-g52.md and follow all instructions in it.'),
    a('h1', -2990, [{ type: 'text', text: 'Holding as the green.' }]),
    u('h2', 40, ROUTER_WAKE('msg_hook0001')),
    a('h3', 42, [{ type: 'text', text: 'Checking the pointer.' }, { type: 'tool_use', id: 't-h3', name: 'Bash', input: { command: 'true' } }]),
    u('h3r', 43, [{ type: 'tool_result', tool_use_id: 't-h3', content: 'ok' }]),
    a('h4', 44, [{ type: 'tool_use', id: 't-h4', name: 'Bash', input: { command: 'mail gm' } }]),
    u('h4r', 45, [{ type: 'tool_result', tool_use_id: 't-h4', content: 'ok' }]),
    a('h5', 50, [{ type: 'text', text: 'Here is the answer you were owed: the deploy is green.' }]),
    u('h6', 51, 'Stop hook feedback:\n[QUEUE-DIGEST] 1 pending message(s) for zz-promote-hook older than 30s — batch-process now (read each via store, ack after processing, stale-banner discipline applies):\n  msg_1 | gm | re: promoted', meta ? { isMeta: true } : {}),
    a('h7', 53, [{ type: 'tool_use', id: 't-h7', name: 'Bash', input: { command: 'ack' } }]),
    u('h7r', 54, [{ type: 'tool_result', tool_use_id: 't-h7', content: 'ok' }]),
    a('h8', 56, [{ type: 'text', text: "Acked gm's reply to the promotion report." }]),
  ];
};

// 526's green with its promote turn's final reply removed: the turn ends in a tool call (no final reply to draw).
const NO_FINAL_526 = G526.filter((l) => !['2026-11-21T15:30:43.000Z', '2026-11-21T15:30:43.000Z'].includes(JSON.parse(l).timestamp));

const S = {
  hide527b: seat('zz-promote-527b', 1, B527, G527, P527, false),
  keep526b: seat('zz-promote-526b', 2, B526, G526, P526, true),
  hide527a: seat('zz-promote-527a', 3, B527, asRouterShape(G527), P527, false),
  keep526a: seat('zz-promote-526a', 4, B526, asRouterShape(G526), P526, true),
  queued527a: seat('zz-promote-527q', 5, B527, withQueued(asRouterShape(G527), WAKE527), P527, false),
  queued527b: seat('zz-promote-527qb', 6, B527, withQueued(G527, WAKE527), P527, false),
  tail527a: seat('zz-promote-527t', 7, B527, withTail(asRouterShape(G527)), P527, false),
  noVerdict527b: seat('zz-promote-527n', 8, B527, G527, P527, null),
  garbage527a: seat('zz-promote-527g', 9, B527, asRouterShape(G527), P527, 'garbage'),
  foreign527a: seat('zz-promote-527f', 10, B527, asRouterShape(G527), P527, 'other-generation'),
  malformed527a: seat('zz-promote-527m', 13, B527, asRouterShape(G527), P527, 'malformed'),
  otherRoot527a: seat('zz-promote-527o', 14, B527, asRouterShape(G527), P527, 'other-root'),
  midTurn527b: seat('zz-promote-527c', 15, B527, G527.filter((l) => JSON.parse(l).timestamp <= '2026-11-21T21:19:55'), P527, null),
  card527a: seat('zz-promote-527d', 16, B527, withCard(asRouterShape(G527), WAKE527), P527, false),
  hookMeta: seat('zz-promote-hook', 17, B527, hookGreen(true), P527, true),
  hookPlain: seat('zz-promote-hookp', 18, B527, hookGreen(false), P527, true),
  repromoted527a: seat('zz-promote-527r', 19, B527, asRouterShape(G527), P527, 'other-promotion'),
  answer527a: seat('zz-promote-527y', 20, B527, withAnswer(asRouterShape(G527), WAKE527), P527, false),
  hide512b: seat('zz-promote-512b', 21, B512, G512, P512, false),
  noPromotedAt527a: seat('zz-promote-527p', 22, B527, asRouterShape(G527), P527, 'no-promoted-at'),
  noBlue527a: seat('zz-promote-527x', 11, null, asRouterShape(G527), P527, false),
  noFinal526a: seat('zz-promote-526nf', 12, B526, asRouterShape(NO_FINAL_526), P526, true),
};
db.close();

const { default: router, normalizeTranscript, settlePromoteTurns, owedVerdict } = await import('./chat-transcript.js');
const { promoteWake, promoteEnvelopeRest, wakeKind } = await import('../lib/audience.js');

async function chat(name: string): Promise<any> {
  const express = (await import('express')).default;
  const a = express();
  a.use('/api/agents', router);
  const server = a.listen(0);
  try {
    return await (await fetch(`http://127.0.0.1:${(server.address() as any).port}/api/agents/${name}/transcript?limit=500`)).json();
  } finally {
    server.close();
  }
}
/** The green's part of the chat: items at or after the promotion that come from the green's log. */
const greenPart = (env: any, s: { promoted: string; lines: string[] }) => {
  const uuids = new Set(s.lines.map((l) => JSON.parse(l).uuid));
  uuids.add('op-queued');
  return env.items.filter((i: any) => uuids.has(i.uuid));
};
const texts = (items: any[]) => items.filter((i: any) => i.kind === 'text').map((i: any) => `${i.role}:${i.text}`);
const PROMOTE_REPLY_527 = 'Narration: I am canonical now, verified (21:19:57Z).';
const PROMOTE_REPLY_526 = 'Narration: I am canonical now, verified (15:30:43Z).';

// ---- the classifier ---------------------------------------------------------------------------------------------
test('promoteWake: both real shapes alone, in every emitter variant', () => {
  for (const t of [DIGEST_527, DIGEST_529, `${DIGEST_529}  `, ROUTER_WAKE('msg_f0000039_00000057'),
    '[MSG from identity-store | high] (sent 3m ago) [PROMOTE] you are canonical — verify against the DB, do not take this on trust — Read /tmp/agent-msg-msg_f0000039_00000057.md and act on it. To reply: python3 ~/scripts/agent-orchestra/msg_store.py send --from YOUR_AGENT_ID --to identity-store --type reply --subject \'re: [PROMOTE] you are canonical — verify aga\' --body \'your reply\'',
    '[MSG from identity-store | high] [SENT 13m AGO — world may have moved since; verify current state before acting on this] [PROMOTE] you are canonical — verify against the DB — Read /tmp/agent-msg-m.md and act on it.']) {
    assert.equal(promoteWake(t), true, t.slice(0, 80));
  }
});

test('promoteWake is tight: anything else in the input is not the wake alone', () => {
  for (const t of [
    `${ROUTER_WAKE('msg_x')} is the deploy done?`,                                         // operator words after it
    `${DIGEST_529}\n  msg_1 | gm | re: deploy`,                                            // a second digest row
    DIGEST_529.replace('1 pending', '2 pending'),
    DIGEST_529.replace('| identity-store |', '| gm |'),                                    // another sender
    DIGEST_529.replace('Stop hook feedback:\n', ''),
    ROUTER_WAKE('msg_x').replace('identity-store | critical', 'somebody | high'),
    `[fwd from gm] ${ROUTER_WAKE('msg_x')}`,
    `look at this: ${ROUTER_WAKE('msg_x')}`,
    '[PROMOTE] you are canonical',
    'what does [PROMOTE] you are canonical mean?',
  ]) assert.equal(promoteWake(t), false, t.slice(0, 90));
  assert.equal(promoteEnvelopeRest(`${ROUTER_WAKE('msg_x')} is the deploy done?`), 'is the deploy done?');
  assert.equal(promoteEnvelopeRest(ROUTER_WAKE('msg_x')), null);
  // the v4 classes are unchanged
  assert.equal(wakeKind(ROUTER_WAKE('msg_x')), 'rotation');
  assert.equal(wakeKind(DIGEST_529), 'beat');
});

// ---- real fixtures: both branches, both shapes -----------------------------------------------------------------
/** The promote turn's items: from the wake line to the next drawn opener. */
const promoteTurn = (g: any[]) => {
  const k = g.findIndex((i: any) => i.wake === 'promote');
  if (k < 0) return [];
  let end = k + 1;
  while (end < g.length && !(g[end].kind === 'text' && g[end].role === 'user' && !g[end].mid_turn && !g[end].queued)) end++;
  return g.slice(k, end);
};

for (const [label, s, wake] of [['shape b (Stop-hook digest)', 'hide527b', WAKE527], ['shape a (router line)', 'hide527a', WAKE527]] as const) {
  test(`swap 527, ${label}: verdict owed:false: no reply emitted, the turn's work stays in the strip (review v4 B-1, v5 A-1)`, async () => {
    const env = await chat(S[s].name);
    const g = greenPart(env, S[s]);
    const turn = promoteTurn(g);
    assert.equal(turn[0]?.ts, wake);
    assert.deepEqual([turn[0].text, turn[0].owed], ['', false], 'the wake line stays hidden');
    assert.ok(!turn.some((i: any) => (i.kind === 'text' && i.role === 'assistant') || i.kind === 'thinking'),
      'no reply and no thinking of the turn is emitted');
    assert.ok(!turn.some((i: any) => i.audience === 'user'), 'nothing in it is for the operator');
    assert.ok(!JSON.stringify(env).includes(PROMOTE_REPLY_527), 'the recap is not in the payload');
    const calls = turn.filter((i: any) => i.kind === 'tool_use');
    assert.ok(calls.length >= 10, `its tool calls stay in the strip (${calls.length})`);
    assert.ok(!texts(g).includes(`assistant:${PROMOTE_REPLY_527}`));
    assert.ok(!JSON.stringify(env).includes('[PROMOTE]'));
  });
}

for (const [label, s] of [['shape b (Stop-hook digest)', 'keep526b'], ['shape a (router line)', 'keep526a']] as const) {
  test(`swap 526, ${label}: verdict owed:true (his message IN PROGRESS): the turn stays, its final reply is for him`, async () => {
    const env = await chat(S[s].name);
    const g = greenPart(env, S[s]);
    const opener = g.find((i: any) => i.wake === 'promote');
    assert.ok(opener, 'the promote turn is in the chat');
    assert.deepEqual([opener.ts, opener.text, opener.owed], [WAKE526, '', true], 'its wake line stays hidden');
    const reply = g.find((i: any) => i.kind === 'text' && i.text === PROMOTE_REPLY_526);
    assert.ok(reply, 'the turn\'s final reply is there');
    assert.equal(reply.audience, 'user', 'and drawn for the operator');
    assert.ok(!JSON.stringify(env).includes('[PROMOTE]'), 'the wake text itself never shows');
    assert.ok(!env.render_items.some((n: any) => n.kind === 'user' && n.text === ''), 'nothing draws the empty line');
    const turn = g.filter((i: any) => i.ts >= WAKE526 && i.ts < '2026-11-21T15:31:10');
    assert.deepEqual(turn.filter((i: any) => i.audience === 'user' && i.kind === 'text').map((i: any) => i.text),
      [PROMOTE_REPLY_526], 'only the final reply is drawn: no mid-turn note');
  });
}

// ---- an operator message inside the wake turn ------------------------------------------------------------------
for (const [label, s] of [['shape a', 'queued527a'], ['shape b', 'queued527b']] as const) {
  test(`${label}: the wake plus an operator message typed while it ran: the turn stays (nothing owed before)`, async () => {
    const g = greenPart(await chat(S[s].name), S[s]);
    const q = g.find((i: any) => i.uuid === 'op-queued');
    assert.ok(q && q.text === 'also, is the deploy done?', 'his message is in the chat');
    assert.ok(g.some((i: any) => i.wake === 'promote' && i.owed), 'the turn it landed in stays');
    assert.ok(texts(g).includes(`assistant:${PROMOTE_REPLY_527}`), 'with the reply that followed his message');
  });
}

test('shape a with his words typed after the envelope: his words open a visible turn, the envelope never shows', async () => {
  const env = await chat(S.tail527a.name);
  const g = greenPart(env, S.tail527a);
  const opener = g.find((i: any) => i.ts === WAKE527 && i.role === 'user');
  assert.ok(opener, 'the turn is in the chat');
  assert.equal(opener.text, 'is the deploy done?');
  assert.equal(opener.wake, undefined);
  assert.ok(!JSON.stringify(env).includes('[PROMOTE]'));
  assert.ok(!JSON.stringify(g.filter((i: any) => i.ts === WAKE527)).includes('agent-msg-'), 'no envelope on his line');
  assert.equal(g.find((i: any) => i.text === PROMOTE_REPLY_527)?.audience, 'user', 'a person\'s turn: his reply is drawn');
});

// ---- the verdict file: the only source, read for this generation only ------------------------------------------
for (const [label, s] of [['no verdict (a swap before verdicts existed)', 'noVerdict527b'],
  ['an unreadable verdict', 'garbage527a'], ['a verdict whose owed is not a boolean', 'malformed527a'],
  ['a verdict naming another generation', 'foreign527a'], ['a verdict naming another root', 'otherRoot527a']] as const) {
  test(`${label}: not known, so the promote turn stays (left out only on positive evidence)`, async () => {
    const g = greenPart(await chat(S[s].name), S[s]);
    assert.ok(g.some((i: any) => i.wake === 'promote' && i.owed), label);
    assert.equal(g.find((i: any) => i.text === PROMOTE_REPLY_527)?.audience, 'user');
  });
}

test('the previous generation\'s log is never needed: no blue file on the box, verdict owed:false, still folded', async () => {
  const g = greenPart(await chat(S.noBlue527a.name), S.noBlue527a);
  const turn = promoteTurn(g);
  assert.equal(turn[0]?.owed, false);
  assert.ok(!texts(g).includes(`assistant:${PROMOTE_REPLY_527}`));
});

test('a kept promote turn that ends in a tool call draws nothing for the operator (no final reply)', async () => {
  const g = greenPart(await chat(S.noFinal526a.name), S.noFinal526a);
  assert.ok(g.some((i: any) => i.wake === 'promote' && i.owed));
  const turn = g.filter((i: any) => i.ts >= WAKE526 && i.ts < '2026-11-21T15:31:10');
  assert.ok(turn.length > 3);
  assert.deepEqual(turn.filter((i: any) => i.kind === 'text' && i.role === 'assistant' && i.audience === 'user'), [],
    'the mid-turn notes ("Narration ... 15:30:15", "reply 15:30:34") are not a final reply');
});

test('owedVerdict reads only a well-formed file for exactly this root and generation', () => {
  const o = process.env.ORCHESTRA_DIR!;
  assert.equal(owedVerdict(S.hide527b.name, S.hide527b.greenId, o), false);
  assert.equal(owedVerdict(S.keep526b.name, S.keep526b.greenId, o), true);
  assert.equal(owedVerdict(S.noVerdict527b.name, S.noVerdict527b.greenId, o), null);
  assert.equal(owedVerdict(S.garbage527a.name, S.garbage527a.greenId, o), null);
  assert.equal(owedVerdict(S.foreign527a.name, S.foreign527a.greenId, o), null);
  assert.equal(owedVerdict(S.malformed527a.name, S.malformed527a.greenId, o), null);
  assert.equal(owedVerdict('../etc', 1, o), null);
  assert.equal(owedVerdict(S.otherRoot527a.name, S.otherRoot527a.greenId, o), null, 'fields must match the file name');
  // a well-formed file whose root is outside the registry alphabet is still refused (same rule as the Python writer)
  for (const bad of ['a.b', 'gm\u00e9', 'x'.repeat(65), '-x']) {
    writeFileSync(join(o, 'state', 'agent-handoffs', 'owed', `${bad}.1.json`),
      JSON.stringify({ schema: 'owed-verdict/1', root: bad, generation_id: 1, owed: false }));
  }
  for (const bad of ['gm\n', 'gm\u00e9', '..', 'a.b', '-x', 'x'.repeat(65), '']) assert.equal(owedVerdict(bad, 1, o), null, JSON.stringify(bad));
  assert.equal(owedVerdict(S.hide527b.name, null, o), null);
  assert.equal(owedVerdict(null, 3, o), null);
});

test('settlePromoteTurns: a second promote wake, or one before the promotion, always goes; owed:false folds the first; no promotion time: kept', () => {
  const T0 = '2026-11-21T12:00:00Z';
  const w = (uuid: string, ts: string) => ({ kind: 'text', role: 'user', text: '', wake: 'promote', uuid, ts });
  const r = (uuid: string, ts: string) => ({ kind: 'text', role: 'assistant', text: `reply ${uuid}`, uuid, ts });
  const items = [w('early', '2026-11-21T11:59:00Z'), r('r0', '2026-11-21T11:59:01Z'),
    w('first', '2026-11-21T12:00:05Z'), r('r1', '2026-11-21T12:00:06Z'),
    w('second', '2026-11-21T12:05:00Z'), r('r2', '2026-11-21T12:05:01Z')];
  assert.deepEqual(settlePromoteTurns(items, T0, true).map((i: any) => i.uuid), ['first', 'r1']);
  // owed:false keeps the first turn (review v4 B-1) without its reply (review v5 A-1); the early and second wakes go
  const q = settlePromoteTurns(items, T0, false);
  assert.deepEqual(q.map((i: any) => [i.uuid, i.owed ?? null]), [['first', false]]);
  // no promotion time (registry lookup failed, review v3 F4): not known, so the first promote turn is KEPT unless a
  // verdict says nothing was owed
  assert.deepEqual(settlePromoteTurns(items, null, true).map((i: any) => i.uuid), ['early', 'r0']);
  assert.deepEqual(settlePromoteTurns(items, null, null).map((i: any) => i.uuid), ['early', 'r0']);
  assert.deepEqual(settlePromoteTurns(items, null, false).map((i: any) => i.uuid), ['early']);
  let asked = 0;
  settlePromoteTurns([r('x', T0)], T0, () => { asked++; return true; });
  assert.equal(asked, 0, 'the predecessor is read only when a promote turn exists');
});

// ---- the one-log builder (stream, export) agrees with the poll route --------------------------------------------
test('the live stream / export cut the same: 527 without the promote turn, 526 with it', () => {
  for (const s of [S.hide527a, S.hide527b]) {
    const items = normalizeTranscript(s.lines, s.name, s.green, 2000).items;
    assert.ok(!texts(items).includes(`assistant:${PROMOTE_REPLY_527}`), s.name);
  }
  for (const s of [S.keep526a, S.keep526b]) {
    const items = normalizeTranscript(s.lines, s.name, s.green, 2000).items;
    assert.equal(items.find((i: any) => i.text === PROMOTE_REPLY_526)?.audience, 'user', s.name);
  }
});

// ---- review v3 F1-F3 ---------------------------------------------------------------------------------------------
test('F1: a kept promote turn polled mid-turn (527 log cut at 21:19:55, no verdict, no person turn yet) draws no interim note', async () => {
  const g = greenPart(await chat(S.midTurn527b.name), S.midTurn527b);
  assert.ok(g.some((i: any) => i.wake === 'promote' && i.owed), 'kept (no verdict)');
  assert.ok(G527.some((l) => JSON.parse(l).timestamp === '2026-11-21T21:19:45.000Z'), 'the 21:19:45 note is in the log');
  assert.ok(!g.some((i: any) => i.ts === '2026-11-21T21:19:45.000Z' && (i.kind === 'text' || i.kind === 'thinking')),
    'a note with a tool call after it is not the final reply: not emitted (v5 A-1; v3 F1)');
  assert.deepEqual(g.filter((i: any) => i.ts >= WAKE527 && i.kind === 'text' && i.role === 'assistant' && i.audience === 'user'), []);
});

for (const [label, s] of [['isMeta Stop-hook line', 'hookMeta'], ['plain Stop-hook line', 'hookPlain']] as const) {
  test(`F2 (${label}): a hook continuation after the owed reply ends it: the reply is drawn, the ack is not`, async () => {
    const env = await chat(S[s].name);
    const g = greenPart(env, S[s]);
    const by = (t: string) => g.find((i: any) => i.text === t);
    assert.equal(by('Here is the answer you were owed: the deploy is green.')?.audience, 'user', 'the owed answer is drawn');
    assert.equal(by("Acked gm's reply to the promotion report."), undefined, 'the ack after the hook is not emitted');
    assert.equal(by('Checking the pointer.'), undefined, 'nor the note before the first call');
    assert.ok(!env.items.some((i: any) => i.kind === 'hook_continuation'), 'the marker never leaves the server');
    assert.ok(!JSON.stringify(env).includes('QUEUE-DIGEST'));
    const stream = normalizeTranscript(S[s].lines, S[s].name, S[s].green, 2000).items;
    assert.equal(stream.find((i: any) => i.text === 'Here is the answer you were owed: the deploy is green.')?.audience, 'user');
    assert.ok(!stream.some((i: any) => i.kind === 'hook_continuation'));
  });
}

test('F3: a decision-card answer relayed while the promote turn ran keeps the turn, even with owed:false', async () => {
  const g = greenPart(await chat(S.card527a.name), S.card527a);
  const uuids = new Set(g.map((i: any) => i.uuid));
  assert.ok(g.some((i: any) => i.wake === 'promote' && i.owed), 'the turn stays');
  assert.ok([...uuids].some((u) => String(u).startsWith('op-card')) || JSON.stringify(g).includes('DECISION ANSWERED'),
    'his card answer is in the chat');
});

// ---- review v4 B-2, T-1 ------------------------------------------------------------------------------------------
test('B-2: a verdict computed for another promotion of the same generation is no verdict: the turn is kept', async () => {
  const g = greenPart(await chat(S.repromoted527a.name), S.repromoted527a);
  assert.ok(g.some((i: any) => i.wake === 'promote' && i.owed === true), 'kept as unknown (not folded by the stale false)');
  assert.equal(g.find((i: any) => i.text === PROMOTE_REPLY_527)?.audience, 'user');
  const stream = normalizeTranscript(S.repromoted527a.lines, S.repromoted527a.name, S.repromoted527a.green, 2000).items;
  assert.ok(stream.some((i: any) => i.wake === 'promote' && i.owed === true));
  const o = process.env.ORCHESTRA_DIR!;
  assert.equal(owedVerdict(S.repromoted527a.name, S.repromoted527a.greenId, o, P527), null);
  assert.equal(owedVerdict(S.repromoted527a.name, S.repromoted527a.greenId, o), false, 'without a promotion time to compare');
  assert.equal(owedVerdict(S.hide527b.name, S.hide527b.greenId, o, P527), false, 'the matching promotion still reads');
});

test('T-1: in a promote turn where the operator typed, the seat\'s answer to him is drawn, not folded', async () => {
  const g = greenPart(await chat(S.answer527a.name), S.answer527a);
  const ask = g.find((i: any) => i.uuid === 'op-asks'), ans = g.find((i: any) => i.uuid === 'op-answer');
  assert.ok(ask && ans, 'his message and the answer are in the chat');
  assert.deepEqual([ans.kind, ans.audience, ans.promote_quiet ?? false], ['text', 'user', false]);
  const before = g.filter((i: any) => i.ts >= WAKE527 && i.ts < ask.ts && i.role === 'assistant' && (i.kind === 'text' || i.kind === 'thinking'));
  assert.deepEqual(before, [], 'the wake\'s own notes before his message are not emitted');
});

// ---- review v5 A-1: what the WEB draws, through the dashboard's own grouping code -----------------------------------
// The live web chat draws every assistant text whatever its tag and an edge thinking block as a "💭" line; only the
// folded strip (tool_group) hides work. This runs the dashboard's real buildRenderList -> markResults -> groupToolRuns
// on the API's poll output for real swaps 512 (closing recap after the last call: the shape that leaked in v8), 526
// and 527, and asserts that nothing from the promote turn is drawn outside a strip except 526's owed final reply.
const DASH = new URL('../../../dashboard/src/lib/', import.meta.url).pathname;
const hasDash = existsSync(join(DASH, 'toolGroups.ts'));
test('A-1: through the dashboard grouping, a promote turn draws nothing but 526\'s final reply', { skip: !hasDash && 'no dashboard source next to the api' }, async () => {
  const { buildRenderList } = await import(join(DASH, 'transcript.ts'));
  const { groupToolRuns } = await import(join(DASH, 'toolGroups.ts'));
  const { markResults } = await import(join(DASH, 'threadKit/results.ts'));
  const cases: [string, typeof S.hide512b, string, string[]][] = [
    ['512 owed:false', S.hide512b, WAKE512, []],
    ['527 owed:false', S.hide527b, WAKE527, []],
    ['526 owed:true', S.keep526b, WAKE526, [PROMOTE_REPLY_526]],
  ];
  for (const [label, s, wake, allowed] of cases) {
    const env = await chat(s.name);
    const g = greenPart(env, s);
    const turn = promoteTurn(g);
    assert.equal(turn[0]?.ts, wake, `${label}: the promote turn is in the chat`);
    assert.ok(turn.some((i: any) => i.kind === 'tool_use'), `${label}: its work is there`);
    const inTurn = new Set(turn.map((i: any) => i.uuid));
    const nodes = groupToolRuns(markResults(buildRenderList(env.items), s.name));
    const drawn: string[] = [];
    for (const n of nodes as any[]) {
      if (n.kind === 'tool_group') continue;                         // the folded strip
      // render keys are "<uuid>:<n>": a node belongs to the promote turn by its item's uuid (the blue's stitched tail,
      // drawn before it, is the previous generation's turn, not this one)
      if (!inTurn.has(String(n.key ?? '').split(':')[0])) continue;
      if (n.kind === 'user' && !String(n.text ?? '').trim()) continue;
      drawn.push(n.kind === 'assistant' ? String(n.text) : `${n.kind}: ${String(n.text ?? n.tool ?? '').slice(0, 60)}`);
    }
    assert.deepEqual(drawn, allowed, `${label}: drawn outside a strip`);
  }
});

test('review v5 B-4: a verdict with no promoted_at is refused while this promotion\'s time is known (kept)', async () => {
  const g = greenPart(await chat(S.noPromotedAt527a.name), S.noPromotedAt527a);
  assert.ok(g.some((i: any) => i.wake === 'promote' && i.owed === true), 'kept as unknown');
  assert.equal(owedVerdict(S.noPromotedAt527a.name, S.noPromotedAt527a.greenId, process.env.ORCHESTRA_DIR!, P527), null);
});
