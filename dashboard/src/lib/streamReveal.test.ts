/**
 * A streamed reply, revealed word by word (SMOOTH_STREAM_SPEC.md): what can be drawn as the words arrive
 * (lib/threadKit/streamMarkdown.ts), the units the reveal releases, the pacing (lib/reveal.ts), a live block's
 * stable id (lib/threadKit/liveBlocks.ts) and the follow glide (lib/useStickToBottom.ts).
 *
 * Run: npx tsx --test src/lib/streamReveal.test.ts   (from dashboard/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CODE_FOLD_LINES, StreamPlan, planText, revealReady, revealSafeLength } from './threadKit/streamMarkdown.ts';
import { unclosedInline } from './threadKit/markdown.ts';
import { CATCH_UP_MS, RevealPacer } from './reveal.ts';
import { assignLiveIds, stableLiveBlocks } from './threadKit/liveBlocks.ts';
import { glideStep } from './useStickToBottom.ts';

const safe = (t: string) => t.slice(0, revealSafeLength(t));

test('a word still being written waits for the space after it; the end of the turn shows everything', () => {
  assert.equal(safe('Hello wor'), 'Hello ');
  assert.equal(safe('Hello world '), 'Hello world ');
  assert.equal(safe('One.\nTwo'), 'One.\n');
  assert.equal(revealSafeLength('Hello wor', true), 'Hello wor'.length);
  assert.equal(revealReady('Hel'), false);
  assert.equal(revealReady('Hello '), true);
});

test('a line that could still become a list, a heading or a fence waits; a block is never drawn without a word', () => {
  assert.equal(safe('Intro.\n\n-'), 'Intro.\n\n');
  assert.equal(safe('Intro.\n\n- it'), 'Intro.\n\n');
  assert.equal(safe('Intro.\n\n- item '), 'Intro.\n\n- item ');
  assert.equal(safe('1'), '');
  assert.equal(safe('1. '), '');
  assert.equal(safe('1. One '), '1. One ');
  assert.equal(safe('10 apples '), '10 apples ');
  assert.equal(safe('##'), '');
  assert.equal(safe('## Res'), '');
  assert.equal(safe('## Results '), '## Results ');
  assert.equal(safe('> quo'), '');
  assert.equal(safe('Run:\n\n``'), 'Run:\n\n');
  assert.equal(revealReady('- '), false);
});

test('fenced code shows a whole line at a time, once its first line is in; a closed fence is done', () => {
  assert.equal(safe('Run:\n\n```sh'), 'Run:\n\n');
  assert.equal(safe('Run:\n\n```sh\n'), 'Run:\n\n');
  assert.equal(safe('Run:\n\n```sh\nls -la\ncat ex'), 'Run:\n\n```sh\nls -la\n');
  assert.equal(safe('```sh\nls\n```\nAfter wor'), '```sh\nls\n```\nAfter ');
});

test('a table appears whole, once a line that is not part of it follows; a line with a pipe waits for the next', () => {
  const t = '| a | b |\n|---|---|\n| 1 | 2 |\n';
  assert.equal(safe(t), '');
  assert.equal(safe(`${t}| 3 | 4 |\n`), '');
  assert.equal(safe(`${t}\nNext `), `${t}\nNext `);
  assert.equal(safe('Intro.\n\n| a | b'), 'Intro.\n\n');
  assert.equal(safe('Intro.\n\n| a | b |\n|--'), 'Intro.\n\n');
});

test('an inline mark that could still close waits from the start of its word: bold, code, italic, a link', () => {
  assert.equal(safe('See the **bold'), 'See the ');
  assert.equal(safe('See the **bold words '), 'See the ');
  assert.equal(safe('See the **bold words** now '), 'See the **bold words** now ');
  assert.equal(safe('See the **bold** and `co'), 'See the **bold** and ');
  assert.equal(safe('Read [docs](https://exa'), 'Read ');
  assert.equal(safe('Read [docs](https://x.y) now '), 'Read [docs](https://x.y) now ');
  assert.equal(safe('a*b c '), '');
  assert.equal(safe('5 * 3 = 15 '), '5 * 3 = 15 ');
  // a mark left open earlier in the block, before a closed one, still waits (it could close round the later one)
  assert.equal(unclosedInline('`x **b** c'), 0);
  assert.equal(unclosedInline('plain **b** text'), -1);
  // in a list only its last item waits
  assert.equal(safe('- a*b\n- two three '), '- a*b\n- two three ');
});

test('units: each word, an inline code span, a link and a chip; a list item\'s marker comes with its first word', () => {
  const { plans, next } = planText('Hi **you** see `x` [d](https://e.f)\n\n- a b\n- c');
  assert.equal(next, 8);
  assert.equal(plans[0].t, 'para');
  const list = plans[1];
  assert.equal(list.t, 'list');
  if (list.t === 'list') assert.deepEqual(list.items.map((i) => i.u), [5, 7]);
  // a long fence: its first lines, then its "more lines" row, as the finished block is drawn folded
  const code = Array.from({ length: CODE_FOLD_LINES + 2 }, (_, i) => `line ${i}`).join('\n');
  const c = planText(`\`\`\`\n${code}\n\`\`\``).plans[0];
  assert.equal(c.t, 'code');
  if (c.t === 'code') { assert.equal(c.lines.length, CODE_FOLD_LINES); assert.equal(c.fold, CODE_FOLD_LINES); }
  // a table: one unit
  assert.equal(planText('| a | b |\n|---|---|\n| 1 | 2 |').next, 1);
});

test('a plan only grows: finished blocks are planned once (the same objects), units never renumber', () => {
  const p = new StreamPlan();
  const full = 'First paragraph here.\n\nSecond one, with **bold** words.\n\n- one\n- two\n\nEnd of it.';
  let prevUnits = 0;
  let firstChunk: unknown = null;
  for (let n = 1; n <= full.length; n++) {
    assert.ok(p.update(full.slice(0, n), false));
    assert.ok(p.units >= prevUnits, `units never shrink (at ${n})`);
    prevUnits = p.units;
    if (p.chunks.length && !firstChunk) firstChunk = p.chunks[0].plans;
    if (firstChunk) assert.equal(p.chunks[0].plans, firstChunk, 'a finished block is never planned again');
  }
  p.update(full, true);
  const done = planText(full).next;
  assert.equal(p.units, done, 'the same units as the finished text');
  // the finished message (trimmed) is the same reply; a different text is not
  const q = new StreamPlan();
  q.update('All done. ', false);
  assert.ok(q.update('All done.', true));
  assert.equal(new StreamPlan().update('x', false), true);
  const r = new StreamPlan(); r.update('Hello there ', false);
  assert.equal(r.update('Goodbye', false), false);
});

test('pacing: a burst is shown within ~300 ms however big; a released unit keeps its release time', () => {
  for (const burst of [5, 20, 60, 200]) {
    const p = new RevealPacer();
    p.setKnown(burst);
    let t = 0;
    while (p.released < p.known && t < 5000) { p.tick(t); t += 16; }
    assert.ok(t <= CATCH_UP_MS + 32, `${burst} units shown by ${t} ms`);
    assert.equal(p.releasedAt.length, burst);
  }
  const p = new RevealPacer();
  p.setKnown(3); p.tick(100);
  assert.equal(p.releasedAt[0], 100);
  p.setKnown(10); p.flush();
  assert.equal(p.released, 10);
  p.setKnown(4);
  assert.equal(p.known, 10, 'never fewer than released');
});

test('a live block keeps its id while it grows, when its uuid arrives, and when the blocks before it land', () => {
  let n = 0;
  const id = () => `b${++n}`;
  let a = assignLiveIds([], [{ uuid: null, text: 'Hel' }], id);
  assert.deepEqual(a, [{ id: 'b1', uuid: null, text: 'Hel' }]);
  a = assignLiveIds(a, [{ uuid: 'u1', text: 'Hello.' }, { uuid: null, text: 'Next' }], id);
  assert.deepEqual(a.map((b) => b.id), ['b1', 'b2']);
  a = assignLiveIds(a, [{ uuid: null, text: 'Next words' }], id);       // the first landed
  assert.deepEqual(a.map((b) => b.id), ['b2']);
  a = assignLiveIds(a, [{ uuid: null, text: 'Other' }], id);            // not the same words: a new block
  assert.deepEqual(a.map((b) => b.id), ['b3']);
  // per agent, and forgotten when nothing is live
  const x = stableLiveBlocks('agent-x', [{ uuid: null, text: 'Hi' }]);
  assert.equal(stableLiveBlocks('agent-x', [{ uuid: null, text: 'Hi there' }])[0].id, x[0].id);
  stableLiveBlocks('agent-x', []);
  assert.notEqual(stableLiveBlocks('agent-x', [{ uuid: null, text: 'Hi there' }])[0].id, x[0].id);
});

test('the follow glide: a share of the way each frame, a few pixels for a line, never back up', () => {
  const first = glideStep(0, 20, 1000 / 60);
  assert.ok(first > 0 && first < 4, `a new line: ${first.toFixed(2)} px the first frame`);
  let y = 0;
  for (let i = 0; i < 60; i++) y = glideStep(y, 20, 1000 / 60);
  assert.equal(y, 20, 'there within a second');
  assert.equal(glideStep(50, 20, 16), 50, 'never up');
  assert.ok(glideStep(0, 200, 1000 / 60) <= 6.01, 'a tall arrival is followed a few pixels a frame');
  assert.ok(glideStep(0, 200, 120) <= 6.01, 'a stalled frame is not made up in one jump');
});
