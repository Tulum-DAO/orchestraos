/**
 * The generation list's logic lives here rather than in the component because the dashboard
 * has no DOM test runner — `npm test` runs src/lib/*.test.ts only. So anything that can be
 * got wrong is a pure function in src/lib and is tested, and the component is a thin render
 * over it. Same split as topologyLines.ts.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  hasGenerationHistory,
  lineageDepthTier,
  describeLineage,
  formatGenerationWhen,
  describeGenerationTiming,
  type GenerationRow,
} from './generations.js';

function row(over: Partial<GenerationRow> = {}): GenerationRow {
  return {
    generation: 1, model: null, spawned_at: null,
    promoted_at: null, retired_at: null, note: null, ...over,
  };
}

test('hasGenerationHistory gates the section on more than one generation', () => {
  // The threshold the card chip already uses: a seat that never rotated shows nothing,
  // rather than 179 of 285 agents each gaining an empty section.
  assert.equal(hasGenerationHistory(2), true);
  assert.equal(hasGenerationHistory(55), true);
  assert.equal(hasGenerationHistory(1), false, 'never rotated -> no section');
  assert.equal(hasGenerationHistory(0), false);
  // Absent, not zero, is the real "no identity DB" signal: the API omits the field
  // rather than sending 0, so undefined must behave as "nothing to show", never as an error.
  assert.equal(hasGenerationHistory(undefined), false);
});

test('formatGenerationWhen never renders Invalid Date', () => {
  // Real rows hold non-date markers in these columns, which is the whole reason this
  // function exists: `new Date(x).toLocaleString()` on junk prints "Invalid Date" into the UI.
  assert.equal(formatGenerationWhen(null), '—');
  assert.equal(formatGenerationWhen(''), '—');
  assert.equal(formatGenerationWhen('not-a-date'), '—');
  assert.equal(formatGenerationWhen('pending'), '—');
  const out = formatGenerationWhen('2026-10-02T05:34:08.621335+00:00');
  assert.notEqual(out, '—');
  assert.ok(!out.includes('Invalid'), `real timestamp should format, got ${out}`);
});

test('describeGenerationTiming prefers retirement, then promotion, then spawn', () => {
  // A retired row is described by when it ENDED; a live one by when it started. Falling back
  // to spawned_at matters because 360 of 950 generation rows have no promoted_at.
  assert.match(
    describeGenerationTiming(row({ retired_at: '2026-10-02T05:34:08Z', promoted_at: '2026-09-30T06:53:36Z' })),
    /^retired /,
  );
  assert.match(describeGenerationTiming(row({ promoted_at: '2026-09-30T06:53:36Z' })), /^promoted /);
  assert.match(describeGenerationTiming(row({ spawned_at: '2026-09-30T02:00:00Z' })), /^promoted /);
  // Nothing usable at all is still a legible row, not a blank or an "Invalid Date".
  assert.equal(describeGenerationTiming(row()), 'promoted —');
});

test('lineageDepthTier maps a lineage to how many sheets the node stacks', () => {
  // The graph's job is "which lineages are deep", not "how deep": the exact number is one
  // click away in the drawer. So depth is encoded coarsely and 55 does not mean 55 sheets.
  assert.equal(lineageDepthTier(undefined), 0, 'not a canonical root -> no stack');
  assert.equal(lineageDepthTier(1), 0, 'never rotated -> looks exactly as it does today');
  assert.equal(lineageDepthTier(2), 1);
  assert.equal(lineageDepthTier(4), 1);
  assert.equal(lineageDepthTier(5), 2);
  assert.equal(lineageDepthTier(55), 2, 'deepest lineage on the fleet still caps at 2 sheets');
  // Threshold agrees with the chip/section gate, so a node never stacks for a lineage the
  // drawer would then refuse to show.
  assert.equal(lineageDepthTier(1), 0);
  assert.equal(hasGenerationHistory(2), lineageDepthTier(2) > 0);
});

test('describeLineage is the node tooltip suffix, and is silent when there is no lineage', () => {
  // Shape alone must not be the only carrier of the meaning.
  assert.equal(describeLineage(undefined), '');
  assert.equal(describeLineage(1), '');
  assert.equal(describeLineage(2), '2 generations');
  assert.equal(describeLineage(55), '55 generations');
});
