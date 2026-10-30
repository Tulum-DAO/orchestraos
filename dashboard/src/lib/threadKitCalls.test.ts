import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  groupCalls, callEndOf, parseVoiceSegments, hasVoiceCallMarker, isCallEndedLine, recordLines, callCounts, fromRecordTool,
  fmtCallDuration, isStaleLive, transcriptText, type CallTurn, type CallRecord,
} from './threadKit/calls.ts';
import { reduce, EMPTY, screenLabel, type ScreenAction, type ScreenState } from './screenCards.ts';

// A stored voice-call thread and its saved record, as the API serves them (names and ids made neutral).
const FIXTURE = JSON.parse(readFileSync(new URL('./fixtures/call-thread.json', import.meta.url), 'utf-8')) as {
  thread: { turns: { role: string; content: string; mode?: string; ts?: number; meta?: { kind?: string; call_id?: string } }[] };
  record: CallRecord;
};
const CALL = 'vc_sample_call01';

/** The thread's turns as Arturo's page reads them (lib/arturoResume): voice turns, and the call-ended tag. */
const turns: CallTurn[] = FIXTURE.thread.turns.map((x) => ({
  role: x.role === 'user' ? 'user' : 'arturo', text: x.content,
  ...(x.mode === 'voice' ? { voice: true } : {}),
  ...(x.meta?.kind === 'call_end' && x.meta.call_id ? { callEnd: x.meta.call_id } : {}),
  ...(typeof x.ts === 'number' ? { ts: x.ts } : {}),
}));

test('the stored call thread folds into one call card, closed by its call-ended line', () => {
  const items = groupCalls(turns, false);
  assert.equal(items.length, 1);
  const card = items[0];
  assert.equal(card.kind, 'call');
  if (card.kind !== 'call') return;
  assert.equal(card.callId, CALL);
  assert.equal(card.live, false);
  assert.equal(card.turns.length, FIXTURE.thread.turns.length - 1);    // every spoken turn; not the ending line
  assert.ok(card.turns.every((t) => !hasVoiceCallMarker(t.text)));
  assert.equal(card.endTs, turns[turns.length - 1].ts);
});

test('the call-ended line is chrome: its marker becomes the card, its words are never shown or copied', () => {
  const end = FIXTURE.thread.turns[FIXTURE.thread.turns.length - 1].content;
  assert.equal(callEndOf({ role: 'arturo', text: end }), CALL);
  const segs = parseVoiceSegments(end);
  assert.deepEqual(segs.map((s) => s.kind), ['prose', 'voicecall']);
  assert.ok(segs[0].kind === 'prose' && isCallEndedLine(segs[0].text));
  assert.equal(callEndOf({ role: 'arturo', text: `Here is what we said. [voice-call: ${CALL} /data/x.json]` }), null);
  assert.equal(callEndOf({ role: 'user', text: end }), null);
});

test('the saved record: words as bubbles, the tool run as the kit tool card, the server\'s "(on the call)" note dropped', () => {
  const lines = recordLines(FIXTURE.record.turns);
  const tools = lines.filter((l) => l.kind === 'tools');
  assert.equal(tools.length, 1);
  assert.ok(tools[0].kind === 'tools');
  if (tools[0].kind !== 'tools') return;
  assert.deepEqual(tools[0].calls.map((c) => [c.name, c.status, c.result]), [['read_screen_context', 'ok', 'dispatched']]);
  assert.ok(lines.every((l) => l.kind !== 'say' || !l.text.startsWith('(on the call)')));
  assert.equal(lines.filter((l) => l.kind === 'say').length, 9);
  assert.deepEqual(callCounts(FIXTURE.record.turns), { talk: 9, tool: 1 });
  assert.equal(fmtCallDuration(FIXTURE.record.started_at, FIXTURE.record.ended_at), '1m 17s');
});

test('consecutive tool runs on a call share one card; inputs as JSON or text; failures read as failed', () => {
  const lines = recordLines([
    { role: 'user', text: 'Check it' },
    { role: 'tool', tool: 'list_agents', input: '{}', status: 'done', result: 'two agents' },
    { role: 'tool', tool: 'read_file', input: '{"path": "/data/sample.md"}', status: 'error', result: 'not found' },
    { role: 'arturo', text: '  ' },
  ]);
  assert.deepEqual(lines.map((l) => l.kind), ['say', 'tools']);
  const run = lines[1];
  if (run.kind !== 'tools') throw new Error('expected a tool run');
  assert.deepEqual(run.calls.map((c) => [c.name, c.status, c.chip]), [['list_agents', 'ok', ''], ['read_file', 'failed', '/data/sample.md']]);
  assert.deepEqual(fromRecordTool({ role: 'tool', tool: 'knowledge', input: 'not json' }, 0).input, { input: 'not json' });
  assert.equal(fromRecordTool({ role: 'tool', tool: 'knowledge' }, 0).status, 'running');
});

test('a live call older than 4 hours reads as stale; the transcript copies as plain lines', () => {
  assert.equal(isStaleLive('live', 1000, 1000 + 4 * 3600 + 1), true);
  assert.equal(isStaleLive('live', 1000, 1000 + 60), false);
  assert.equal(isStaleLive('ended', 1000, 99999), false);
  assert.equal(transcriptText([{ role: 'user', text: 'Hi' }, { role: 'arturo', text: ' Hello ' }, { role: 'arturo', text: ' ' }], 'Sample'),
    'You: Hi\nSample: Hello');
});

test('the pane label names a multi-part card by the part on show, as it pages', () => {
  const card = (n: number, question: string) => ({ id: 'a', kind: 'multipart' as const, from: 'pm-demo', state: 'waiting' as const,
    question: 'Which layout?', part: { n, of: 2, question, select: 'single' as const, options: [], picked: [], typed: '' } });
  const go = (...as: ScreenAction[]) => as.reduce(reduce, EMPTY as ScreenState);
  let s = go({ type: 'report', key: 'k', card: card(1, 'Which layout?'), surface: 'chat' }, { type: 'visible', key: 'k', visible: true, now: 1 });
  assert.equal(screenLabel(s), 'Which layout?');
  s = reduce(s, { type: 'report', key: 'k', card: card(2, 'Which colour?'), surface: 'chat' });
  assert.equal(screenLabel(s), 'Which colour?');
  // A card of one part keeps its own question.
  s = go({ type: 'report', key: 'k', card: { ...card(1, 'Pick one'), part: { ...card(1, 'Pick one').part, of: 1 } }, surface: 'chat' },
    { type: 'visible', key: 'k', visible: true, now: 1 });
  assert.equal(screenLabel(s), 'Which layout?');
});
