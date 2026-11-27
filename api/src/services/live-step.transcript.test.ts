/**
 * A tmux seat's live step from its TRANSCRIPT (gm msg_e8195a72, Shaw focus #2: a live status line that changes at every
 * step). The pane probe sees no tool for a Claude Code pane: measured 14:17Z, 75 of 75 step events were `thinking`
 * through three 6 s Bash calls, and `since` moved every second (from the probe's state_age_s), so the same step was
 * re-sent every second. The session log has each call as it starts, with its own description; the step now comes from
 * there, and the probe keeps only what it alone knows: idle, a held question or permission, or a seat that is down.
 *
 * Fixture: fixtures/steps.json, items as the tailer gives them in the shape of a real seat's two minutes: three calls
 * with descriptions, two narration lines, a task notification. Its words are made up.
 *
 * Run: npx tsx --test src/services/live-step.transcript.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { RESULT_GRACE_MS, SUMMARY_MAX, StepState, sameStep, stepFromTranscript, stepSig } from './live-step.js';

const ITEMS = JSON.parse(readFileSync(new URL('./fixtures/steps.json', import.meta.url), 'utf-8')) as any[];
const upTo = (uuid: string) => ITEMS.slice(0, ITEMS.findIndex((it) => it.uuid === uuid) + 1);
const at = (iso: string) => Date.parse(iso);
// What the probe says for a Claude Code pane while it works: working or thinking, no tool, an age that keeps moving.
const busy = (age = 3) => ({ state: 'thinking', state_age_s: age });

test('a running call: its tool and its own description, verbatim, from the moment it was written', () => {
  const items = upTo('tu-probe');
  assert.deepEqual(stepFromTranscript(items, busy(), at('2026-01-10T10:36:42.000Z')), {
    kind: 'tool', tool: 'Bash', summary: 'Check the probe cache and the call items', described: true,
    since: '2026-01-10T10:36:41.000Z',
  });
});

test('the same step is the same frame: `since` is the item\'s own time, whatever age the probe reports', () => {
  const items = upTo('tu-probe');
  const sigs = new Set([1, 2, 3, 4, 5].map((age) => stepSig(stepFromTranscript(items, busy(age), at('2026-01-10T10:36:42Z') + age * 1000))));
  assert.equal(sigs.size, 1, [...sigs].join(' / '));
});

test('between calls: the words that led into the call just finished, else its description; then the agent\'s latest line', () => {
  // r26 (gm 2026-11-24, C): a result back reads as work, the milestone that led into that call, not "Thinking…".
  assert.deepEqual(stepFromTranscript(upTo('tr-probe'), busy(), at('2026-01-10T10:37:00Z')), {
    kind: 'thinking', summary: 'The reader opens the log again on every beat, which costs a stat and a parse each time, so I will add a cheap check…',
    since: '2026-01-10T10:36:42.000Z' }, 'a result came back: the line that led into it');
  assert.deepEqual(stepFromTranscript(upTo('tr-tail'), busy(), at('2026-01-10T10:36:21Z')),
    { kind: 'thinking', summary: 'Check how often the tailer reads the log', described: true, since: '2026-01-10T10:36:20.000Z' }, 'no words before it: its description');
  assert.deepEqual(stepFromTranscript(upTo('nar-items'), busy(), at('2026-01-10T10:37:33Z')), {
    kind: 'thinking', summary: 'The items carry input.description and a stable ts, so the plan holds; I am checking what the stream sends for each…',
    since: '2026-01-10T10:37:33.000Z',
  });
  const next = stepFromTranscript(upTo('tu-status'), busy(), at('2026-01-10T10:37:34.5Z'))!;
  assert.equal(next.summary, 'Check the stream step and its rail', 'the next call replaces the line');
});

test('words older than a result that came back are not what it is doing now', () => {
  const items = [
    { kind: 'tool_use', id: 'a', tool: 'Bash', input: { command: 'sleep 5', description: 'Wait' }, ts: '2026-01-10T10:40:00.000Z' },
    { kind: 'text', role: 'assistant', text: 'Waiting on the build.', ts: '2026-01-10T10:40:01.000Z' },
    { kind: 'tool_result', tool_use_id: 'a', ts: '2026-01-10T10:40:05.000Z' },
  ];
  assert.deepEqual(stepFromTranscript(items, busy()), { kind: 'thinking', since: '2026-01-10T10:40:05.000Z' });
});

test('a message just received opens the turn: thinking from its time', () => {
  assert.deepEqual(stepFromTranscript(upTo('u-wake'), busy(), at('2026-01-10T10:36:56Z')),
    { kind: 'thinking', since: '2026-01-10T10:36:54.000Z' });
});

test('two calls at once: the one still running is shown, not the one that finished', () => {
  const items = [
    { kind: 'tool_use', id: 'a', tool: 'Read', input: { file_path: '/p/plan.md' }, ts: '2026-01-10T10:40:00.000Z' },
    { kind: 'tool_use', id: 'b', tool: 'Bash', input: { command: 'npm test', description: 'Run the suite' }, ts: '2026-01-10T10:40:01.000Z' },
    { kind: 'tool_result', tool_use_id: 'a', ts: '2026-01-10T10:40:02.000Z' },
  ];
  assert.equal(stepFromTranscript(items, busy(), at('2026-01-10T10:40:03Z'))?.summary, 'Run the suite');
});

test('no description: the input summary as before (not marked described); a description is redacted, one line, capped', () => {
  const s = stepFromTranscript([{ kind: 'tool_use', id: 'a', tool: 'Read', input: { file_path: '/p/plan.md' }, ts: '2026-01-10T10:40:00Z' }], busy())!;
  assert.deepEqual([s.summary, s.described], ['plan.md', undefined]);
  const secret = stepFromTranscript([{ kind: 'tool_use', id: 'a', tool: 'Bash', ts: '2026-01-10T10:40:00Z',
    input: { command: 'x', description: 'Use token Bearer abcdefghijklmnopqrstuvwxyz123456\nthen more' } }], busy())!.summary!;
  assert.ok(!secret.includes('abcdefghijklmnop') && !secret.includes('then more'), secret);
  const long = stepFromTranscript([{ kind: 'tool_use', id: 'a', tool: 'Bash', ts: '2026-01-10T10:40:00Z',
    input: { command: 'x', description: 'word '.repeat(60) } }], busy())!.summary!;
  assert.equal(SUMMARY_MAX, 120);
  assert.ok(long.length <= SUMMARY_MAX && long.endsWith('word…'), `cut at a word: ${long}`);
  const md = stepFromTranscript([{ kind: 'text', role: 'assistant', text: '**Where it stands:** two of three done.\n\nMore.', ts: '2026-01-10T10:40:00Z' }], busy())!;
  assert.equal(md.summary, 'Where it stands: two of three done.', 'Markdown marks are not words');
});

test('the probe keeps what only it knows: idle, a held question, a seat that is down', () => {
  const items = upTo('tu-probe');
  const now = at('2026-01-10T10:36:42Z');
  assert.equal(stepFromTranscript(items, undefined, now), null, 'no verdict: no step');
  assert.equal(stepFromTranscript(items, { state: 'stopped' }, now), null);
  assert.equal(stepFromTranscript(items, { state: 'waiting_permission', state_age_s: 0.2 }, now)?.kind, 'waiting');
  const after = [...ITEMS, { kind: 'text', role: 'assistant', text: 'Done.', ts: '2026-01-10T10:38:10.000Z' }];
  assert.equal(stepFromTranscript(after, { state: 'idle', state_age_s: 20 }, at('2026-01-10T10:38:31Z'))?.kind, 'idle');
  // The probe is a beat behind at a turn's start: a call newer than its "idle" means the agent is working.
  assert.equal(stepFromTranscript(items, { state: 'idle', state_age_s: 30 }, now)?.kind, 'tool');
});

test('a headless seat writing its reply says so (a flag on thinking: no new kind on the wire)', () => {
  const s = new StepState();
  const out = (ev: any) => ({ kind: 'out', wall: '2026-01-10T10:40:00.000Z', ev: { parent_tool_use_id: null, ...ev } });
  s.apply({ kind: 'sup', wall: '2026-01-10T10:40:00.000Z', what: 'state', state: 'working' });
  s.apply(out({ type: 'stream_event', event: { type: 'content_block_start', content_block: { type: 'thinking' } } }));
  assert.deepEqual(s.current(), { kind: 'thinking', since: '2026-01-10T10:40:00.000Z' });
  s.apply(out({ type: 'stream_event', event: { type: 'content_block_start', content_block: { type: 'text' } } }));
  assert.deepEqual(s.current(), { kind: 'thinking', writing: true, since: '2026-01-10T10:40:00.000Z' });
  s.apply(out({ type: 'assistant', message: { content: [{ type: 'tool_use', name: 'Bash', input: { command: 'ls', description: 'List the files' } }] } }));
  assert.deepEqual(s.current(), { kind: 'tool', tool: 'Bash', summary: 'List the files', described: true, since: '2026-01-10T10:40:00.000Z' });
});

// Measured live 2026-11-22 18:45Z, right after r20 went in: the probe said `idle` for this pane while a 6 s Bash call
// ran, with an age that kept the computed start at "now", so the line read Idle (the row hid) and the same idle step was
// re-sent every 250 ms.
test('a call still running is not idle, whatever the probe says (a fresh-looking idle included)', () => {
  const items = upTo('tu-probe');
  const now = at('2026-01-10T10:36:45Z');
  assert.equal(stepFromTranscript(items, { state: 'idle', state_age_s: 0 }, now)?.kind, 'tool');
  assert.equal(stepFromTranscript(items, { state: 'waiting_permission', state_age_s: 0 }, now)?.kind, 'waiting', 'a held permission still wins');
  assert.equal(stepFromTranscript(upTo('tr-probe'), { state: 'idle', state_age_s: 0 }, at('2026-01-10T10:37:20Z'))?.kind, 'idle', 'its result is back and the grace is over: idle stands');
});

test('the same step with only a drifting start is not a new frame; a new call with the same words is', () => {
  const idle = (since: string) => ({ kind: 'idle' as const, since });
  assert.equal(sameStep(idle('2026-01-10T11:45:51.000Z'), idle('2026-01-10T11:45:52.000Z')), true);
  const call = (since: string) => ({ kind: 'tool' as const, tool: 'Bash', summary: 'Wait', described: true as const, since });
  assert.equal(sameStep(call('2026-01-10T11:45:50.000Z'), call('2026-01-10T11:45:50.000Z')), true);
  assert.equal(sameStep(call('2026-01-10T11:45:50.000Z'), call('2026-01-10T11:46:00.000Z')), false);
  assert.equal(sameStep({ kind: 'thinking', summary: 'A', since: 'x' }, { kind: 'thinking', summary: 'B', since: 'x' }), false);
  assert.equal(sameStep(idle('x'), { kind: 'thinking', since: 'x' }), false);
  assert.equal(sameStep(null, idle('x')), false);
});

// Measured live 2026-11-22 18:55Z after r20b: between a result coming back and the next call, the probe read this pane
// as idle for 2-3 s, so the row hid between calls. After a tool result the CLI always goes on (a turn never ends on a
// result), and a narration line (Opus between-call words) is always followed by a call; the probe's idle there is wrong.
// A result the user interrupted ends the turn with no reply, so this holds only for RESULT_GRACE_MS.
test('a result just back, or a between-call line, is the agent working, not idle; a final reply or an old result is idle', () => {
  const fresh = { state: 'idle', state_age_s: 0 };
  assert.equal(RESULT_GRACE_MS, 30_000);
  assert.equal(stepFromTranscript(upTo('tr-probe'), fresh, at('2026-01-10T10:36:44.5Z'))?.kind, 'thinking');
  assert.equal(stepFromTranscript(upTo('nar-items'), fresh, at('2026-01-10T10:37:33Z'))?.kind, 'thinking', 'narration: a call follows');
  assert.equal(stepFromTranscript(upTo('tr-probe'), fresh, at('2026-01-10T10:37:20Z'))?.kind, 'idle', '38 s after the result: idle stands');
  const final = [...upTo('tr-status'), { kind: 'text', role: 'assistant', text: 'Done.', ts: '2026-01-10T10:37:40.000Z' }];
  assert.equal(stepFromTranscript(final, fresh, at('2026-01-10T10:37:41Z'))?.kind, 'idle', 'a final reply: idle');
});
