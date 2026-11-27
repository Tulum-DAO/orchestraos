/**
 * The live status line at a turn's end (gm msg_8da9ef1f, Shaw 2026-11-23 18:4x ET: "What replaced your 'thinking' was
 * poorly done"). On the gm seat, after a one-turn answer with no calls, the row under the reply showed the REPLY's own
 * first line and stayed, with Stop still lit, while the seat was at its prompt.
 *
 * Cause: a final reply was read as "the agent's latest line" (the step's words), and the probe had read the pane idle
 * while the reply was being written, so the reply's line was "newer than the idle" and won for good. Claude Code marks
 * the end itself: the final reply's message stops end_turn (a reply with a call stops tool_use), and the CLI writes a
 * turn_duration line when the turn is over. turnEndFromLines reads that from the raw lines; stepFromTranscript makes it
 * authoritative (idle, the row goes, Stop reverts).
 *
 * Fixture: fixtures/turn-end.jsonl, in the shape of gm's log 2026-11-24, two minutes (that one-turn answer, then a
 * turn with five calls, a blocking Stop hook in the middle): every line's type, time, stop reason and flags as written;
 * the words, ids, paths and tool outputs made up.
 *
 * Run: npx tsx --test src/services/live-step.turnend.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { finalReplyUuids, stepFromTranscript, turnEndFromLines, type StepBody } from './live-step.js';
import { normalizeTranscript } from '../routes/chat-transcript.js';

const LINES = readFileSync(new URL('./fixtures/turn-end.jsonl', import.meta.url), 'utf-8').split('\n').filter(Boolean);
const at = (iso: string) => Date.parse(iso);
const ts = (i: number) => JSON.parse(LINES[i]).timestamp as string;
const idx = (pred: (o: any) => boolean) => LINES.findIndex((l) => pred(JSON.parse(l)));
const textOf = (o: any) => (Array.isArray(o.message?.content) ? o.message.content.find((b: any) => b.type === 'text')?.text : '') ?? '';

/** What the step beat computes when the log holds lines[0..k]: the tailer's items, its turn end, a probe. */
function stepAt(k: number, probe: { state: string; state_age_s?: number }, now = at(ts(k)) + 200): StepBody | null {
  const lines = LINES.slice(0, k + 1);
  const items = normalizeTranscript(lines, 'gm', 'sid', 150, false, false, null, true).items;
  return stepFromTranscript(items, probe, now, turnEndFromLines(lines), finalReplyUuids(lines));
}
const busy = { state: 'thinking', state_age_s: 1 };
// The gm case: the pane read idle while the reply was being written (its idle start before the reply's line).
const idleEarly = (k: number) => ({ state: 'idle', state_age_s: (at(ts(k)) + 200 - at('2026-01-10T09:40:35Z')) / 1000 });

const REPLY1 = idx((o) => o.type === 'assistant' && textOf(o).startsWith('It checks'));
const DUR1 = idx((o) => o.type === 'system' && o.subtype === 'turn_duration');
const SEEN = idx((o) => o.type === 'assistant' && textOf(o).startsWith('Seen it'));
const HOOK = idx((o) => o.type === 'user' && o.isMeta === true);
const FIND = idx((o) => JSON.stringify(o).includes('Find the change that added'));
const ORDER = idx((o) => JSON.stringify(o).includes('Send the fix order'));
const LAST = LINES.length - 1;

test('A: the final reply landing ends the turn in that tick, whatever the probe says', () => {
  for (const probe of [busy, idleEarly(REPLY1)]) {
    assert.deepEqual(stepAt(REPLY1, probe), { kind: 'idle', since: ts(REPLY1) }, JSON.stringify(probe));
  }
  assert.deepEqual(stepAt(DUR1, busy), { kind: 'idle', since: ts(DUR1) }, 'the turn_duration line');
  assert.equal(stepAt(REPLY1 - 1, busy)?.kind, 'thinking', 'the reply\'s thinking line alone: its words have not landed');
  assert.equal(stepAt(LAST, busy)?.kind, 'idle', 'the tool turn\'s final reply too');
  assert.equal(stepAt(LAST, { state: 'waiting_permission', state_age_s: 0 })?.kind, 'waiting', 'a held prompt still waits');
  assert.equal(stepAt(LAST, { state: 'stopped' }), null, 'a seat that is down has no step');
});

test('A: a blocking Stop hook reopens the turn; its reply was the end until then', () => {
  assert.equal(stepAt(SEEN, busy)?.kind, 'idle');
  assert.equal(stepAt(HOOK, busy)?.kind, 'thinking', 'Stop hook feedback: the agent goes on');
  assert.equal(stepAt(HOOK + 1, busy)?.kind, 'thinking');
});

test('B: no line ever echoes a final reply, at any point in either turn, with either probe', () => {
  const replies = LINES.map((l) => JSON.parse(l)).filter((o) => o.type === 'assistant' && o.message.stop_reason === 'end_turn')
    .map(textOf).filter(Boolean).map((t: string) => t.split('\n')[0].slice(0, 40));
  assert.equal(replies.length, 3);
  for (let k = 0; k < LINES.length; k++) {
    for (const probe of [busy, idleEarly(k)]) {
      const s = stepAt(k, probe);
      for (const r of replies) assert.ok(!(s?.summary ?? '').startsWith(r), `line ${k}: ${s?.summary}`);
    }
  }
});

test('C: a running call is its own description; between calls, the call that just finished (no words of its own)', () => {
  assert.deepEqual(stepAt(FIND, busy), { kind: 'tool', tool: 'Bash', summary: 'Find the change that added the strip',
    described: true, since: ts(FIND) });
  const back = idx((o) => o.type === 'user' && JSON.stringify(o).includes('tool_result') && at(o.timestamp) > at(ts(FIND)));
  assert.deepEqual(stepAt(back, busy), { kind: 'thinking', summary: 'Find the change that added the strip',
    described: true, since: ts(back) });
  assert.equal(stepAt(ORDER, busy)?.summary, 'Send the fix order at high priority');
});

test('turnEndFromLines: what counts and what does not', () => {
  const line = (o: object) => JSON.stringify(o);
  const reply = (stop: string, content: object[]) => line({ type: 'assistant', timestamp: 'T', message: { stop_reason: stop, content } });
  assert.equal(turnEndFromLines([reply('end_turn', [{ type: 'text', text: 'Done.' }])]), 'T');
  assert.equal(turnEndFromLines([reply('stop_sequence', [{ type: 'text', text: 'Done.' }])]), 'T');
  assert.equal(turnEndFromLines([reply('tool_use', [{ type: 'text', text: 'Checking.' }, { type: 'tool_use', name: 'Bash' }])]), null);
  assert.equal(turnEndFromLines([reply('end_turn', [{ type: 'thinking', thinking: '' }])]), null, 'no words yet');
  assert.equal(turnEndFromLines([reply('end_turn', [{ type: 'text', text: 'Done.' }]),
    line({ type: 'assistant', isSidechain: true, timestamp: 'S', message: { stop_reason: 'tool_use', content: [] } })]), 'T', 'a subagent line is not the seat\'s');
  assert.equal(turnEndFromLines([reply('end_turn', [{ type: 'text', text: 'Done.' }]),
    line({ type: 'user', timestamp: 'U', message: { content: 'next' } })]), null, 'a new message opens the next turn');
  assert.equal(turnEndFromLines([line({ type: 'response_item', payload: {} })]), null, 'not a Claude Code log');
  assert.equal(turnEndFromLines(['{not json']), null);
});
