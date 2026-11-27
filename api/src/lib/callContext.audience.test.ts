/**
 * A voice call's opening thread context reads the chat as the operator sees it (callContext.ts chatLines: items with
 * audience "user"). Since every reply of a turn the person opened is drawn (lib/audience.ts, B1 on #706), the call's
 * thread carries every one of those replies, mid-turn ones included, not only the first and the closing reply. A job's
 * turn still gives the call its closing reply only, and a turn mail opened gives it none.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/lib/callContext.audience.test.ts (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tagAudience } from './audience.js';
import { chatLines } from './callContext.js';

let n = 0;
const T = () => `2026-01-10T12:00:${String(++n).padStart(2, '0')}.000Z`;
const u = (text: string) => ({ kind: 'text', role: 'user', text, uuid: `u${n}`, ts: T() });
const a = (text: string) => ({ kind: 'text', role: 'assistant', text, uuid: `a${n}`, ts: T() });
const tool = () => ({ kind: 'tool_use', role: 'assistant', tool: 'Bash', input: { command: 'ls' }, uuid: `t${n}`, ts: T() });
const res = () => ({ kind: 'tool_result', role: 'user', text: 'ok', uuid: `r${n}`, ts: T() });

test('a person turn: every reply of it is in the call\'s thread, mid-turn replies included', () => {
  const items = tagAudience([u('fix the card'), a('looking at it'), tool(), res(), a('found the bug, fixing'), tool(), res(),
    a('done: the card answers now')]);
  assert.deepEqual(chatLines(items, 'Agent').map((l) => `${l.who}: ${l.text}`),
    ['Operator: fix the card', 'Agent: looking at it', 'Agent: found the bug, fixing', 'Agent: done: the card answers now']);
});

test('a job\'s turn gives the call its closing reply only; a turn mail opened gives it none', () => {
  // A person turn first: in a log with none at all, the newest reply is drawn anyway (audience.ts fallback).
  const items = tagAudience([u('hi'), a('hello'),
    u('<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>'),
    a('checking the job'), a('the job passed'),
    u('[MSG from gm | high] (sent 0m ago) status?'), a('replying to gm'), a('sent'),
  ]);
  assert.deepEqual(chatLines(items, 'Agent').map((l) => l.text), ['hi', 'hello', 'the job passed']);
});
