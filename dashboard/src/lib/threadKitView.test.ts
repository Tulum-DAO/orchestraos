import { test } from 'node:test';
import assert from 'node:assert/strict';
import { arturoRows, lastUserKey, showLive, turnKey, type ThreadRow } from './threadKit/view.ts';
import type { ArturoTurnLike } from './threadKit/thread.ts';
import type { FinishedResult } from './threadKit/results.ts';

const result = (key: string): FinishedResult => ({ key, title: `Job ${key}`, summary: 'Done.', status: 'done' } as FinishedResult);
const kinds = (rows: ThreadRow[]) => rows.map((r) => (r.kind === 'message' ? `${r.kind}:${r.role}` : r.kind));

const turns: ArturoTurnLike[] = [
  { id: 1, role: 'user', text: 'Research the sample' },
  { id: 2, role: 'arturo', text: '', result: result('a') },
  { id: 3, role: 'arturo', text: '', result: result('b') },
  { id: 4, role: 'user', text: 'Hello', voice: true, ts: 10 },
  { id: 5, role: 'arturo', text: 'Hi there', voice: true, ts: 11 },
  { id: 6, role: 'arturo', text: 'Call ended (vc_sample_call01). [voice-call: vc_sample_call01 /data/x.json]', callEnd: 'vc_sample_call01', ts: 12 },
  { id: 7, role: 'arturo', text: '', result: result('c') },
  { id: 8, role: 'user', text: 'Thanks', state: 'acked' },
  { id: 9, role: 'arturo', text: 'Any time.', parts: [{ kind: 'text', text: 'Any time.' }] },
];

test('a call folds into one row between thread items; results stack, but never across a call', () => {
  const rows = arturoRows(turns, false, 'Sample');
  assert.deepEqual(kinds(rows), ['message:user', 'result', 'call', 'result', 'message:user', 'message:assistant']);
  const first = rows[1];
  assert.ok(first.kind === 'result');
  if (first.kind !== 'result') return;
  assert.deepEqual(first.results.map((r) => [r.key, r.by]), [['a', 'Sample'], ['b', 'Sample']]);
  const call = rows[2];
  assert.ok(call.kind === 'call' && call.callId === 'vc_sample_call01' && call.turns.length === 2 && call.endTs === 12 && !call.live);
});

test('message keys are the turns\' own, whatever run they sit in', () => {
  const rows = arturoRows(turns, false);
  assert.deepEqual(rows.filter((r) => r.kind === 'message').map((r) => r.key), ['t1', 't8', 't9']);
  const noIds = arturoRows([{ role: 'user', text: 'a' }, { role: 'arturo', text: 'b', voice: true }, { role: 'user', text: 'c' }], false);
  assert.deepEqual(noIds.filter((r) => r.kind === 'message').map((r) => r.key), ['t0', 't2']);
});

test('a call that is on is the last row, its turns so far inside it', () => {
  const rows = arturoRows([{ id: 1, role: 'user', text: 'Hi' }, { id: 2, role: 'user', text: 'spoken', voice: true }], true);
  const last = rows[rows.length - 1];
  assert.ok(last.kind === 'call' && last.live && last.turns.length === 1 && last.callId === null);
  assert.equal(arturoRows([], true).length, 1);
});

test('only the latest turn of the operator shows its send state, a spoken one included', () => {
  assert.equal(lastUserKey(turns), 't8');
  assert.equal(lastUserKey([...turns, { id: 10, role: 'user', text: 'said', voice: true }]), 't10');
  assert.equal(lastUserKey([{ id: 1, role: 'arturo', text: 'x' }]), undefined);
  assert.equal(turnKey({ role: 'user', text: '' }, 4), 't4');
});

test('live text is drawn at the end until the thread holds its message', () => {
  const rows = arturoRows(turns, false);
  assert.equal(showLive(rows, { text: 'Writing…' }), true);
  assert.equal(showLive(rows, { key: 't99', text: 'Writing…' }), true);
  assert.equal(showLive(rows, { key: 't9', text: 'Any time.' }), false);   // the finished turn replaced it
  assert.equal(showLive(rows, { text: '   ' }), false);
  assert.equal(showLive(rows, null), false);
});

import { agentRows } from './threadKit/view.ts';
import type { FeedEntry, Decision } from './decisions.ts';

const node = (key: string, kind = 'assistant') => ({ kind, key, text: key, ts: '2026-10-30T10:00:00Z' }) as unknown as FeedEntry & { kind: 'node' };
const decision = { id: 'apr_0a1b2c3d_1', from_agent: 'pm-demo', kind: 'menu', state: 'pending' } as unknown as Decision;

test('an agent\'s chat as rows: its nodes and cards in feed order, then the sends still waiting for their rows', () => {
  const entries: FeedEntry[] = [
    { kind: 'node', node: node('a', 'user') as never },
    { kind: 'node', node: node('b', 'tool_group') as never },
    { kind: 'decision', decision },
    { kind: 'node', node: node('c', 'tool_group') as never },
  ];
  const rows = agentRows(entries, [{ id: 'p1', display: 'Sample message', kind: 'queuedInTurn' }], 'working');
  assert.deepEqual(rows.map((r) => [r.kind, r.key]), [['node', 'a'], ['node', 'b'], ['decision', 'decision-apr_0a1b2c3d_1'], ['node', 'c'], ['pending', 'pending-p1']]);
  // Every tool run but the last was stopped; the last runs on while the agent works.
  assert.deepEqual(rows.filter((r) => r.kind === 'node').map((r) => (r.kind === 'node' ? r.interrupted : null)), [true, true, false]);
  const pending = rows[4];
  assert.ok(pending.kind === 'pending' && pending.text === 'Sample message' && pending.pending === 'queuedInTurn');
});

test('the last tool run reads as stopped once the agent is stopped; an empty chat has no rows', () => {
  const rows = agentRows([{ kind: 'node', node: node('a', 'tool_group') as never }], [], 'stopped');
  assert.equal(rows[0].kind === 'node' && rows[0].interrupted, true);
  assert.deepEqual(agentRows([], [], 'idle'), []);
});

test('live text on an agent page is drawn until the transcript holds its message', () => {
  const rows = agentRows([{ kind: 'node', node: node('msg-1:0') as never }], [], 'working');
  assert.equal(showLive(rows, { key: 'msg-2:0', text: 'Writing…' }), true);
  assert.equal(showLive(rows, { key: 'msg-1:0', text: 'Writing…' }), false);
});

import { modernMessage } from './threadKit/view.ts';

test('in Modern an agent\'s plain messages are drawn as messages; machine lines, pastes, calls and the rest keep their cards', () => {
  const at = '2026-10-30T10:00:00Z';
  const user = (text: string, extra: Record<string, unknown> = {}) => ({ kind: 'user', key: `u-${text.length}`, text, ts: at, ...extra }) as never;
  const reply = modernMessage({ kind: 'assistant', key: 'a1', text: 'Done: the sample is ready.', ts: at } as never);
  assert.deepEqual([reply?.role, reply?.text, reply?.at, reply?.copyText], ['assistant', 'Done: the sample is ready.', at, 'Done: the sample is ready.']);
  assert.equal(modernMessage(user('Please check the example'))?.role, 'user');
  const image = modernMessage(user('[IMAGE: /x/state/uploads/shot.png] look at this'));
  assert.deepEqual([image?.text, image?.attachments?.[0]?.url], ['look at this', '/uploads/shot.png']);
  for (const n of [
    user('[DECISION ANSWERED apr_0a1b2c3d_1] \'Which size?\' -> Small'),
    user('anything', { machine: 'relay' }),
    user('the session prompt', { isSystem: true }),
    user('waiting', { queued: true }),
    user('[FILE: /tmp/notes.txt] read this'),
    user('Call summary\n[voice-call: vc_sample_call01 /data/x.json]'),
    { kind: 'thinking', key: 't', text: 'hmm' },
    { kind: 'tool_group', key: 'g', children: [] },
  ]) assert.equal(modernMessage(n as never), null, JSON.stringify(n).slice(0, 60));
});

test('Modern rows carry the message; Bubbles rows do not', () => {
  const entries = [{ kind: 'node', node: { kind: 'assistant', key: 'a1', text: 'Hi', ts: '2026-10-30T10:00:00Z' } }] as never;
  const m = agentRows(entries, [], 'idle', { modern: true })[0];
  const b = agentRows(entries, [], 'idle')[0];
  assert.ok(m.kind === 'node' && m.message?.text === 'Hi');
  assert.ok(b.kind === 'node' && b.message === undefined);
});
