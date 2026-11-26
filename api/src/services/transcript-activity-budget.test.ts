/**
 * Re-review of #641: most live session entries carry no cwd, so after an API restart every seat needs the
 * full resolver once. Unbounded, the first /agents poll resolved ~80 seats in one synchronous request.
 * The resolver runs at most RESOLVE_BUDGET times per second; the rest read unknown and resolve later.
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';

const dir = testTmpDir('tact-budget-');
mkdirSync(join(dir, 'state'), { recursive: true });
process.env.HOME = join(dir, 'home');
process.env.TMUX_TMPDIR = join(dir, 'no-tmux');
mkdirSync(process.env.TMUX_TMPDIR, { recursive: true });
delete process.env.TMUX;
process.env.ORCHESTRA_TRANSCRIPT_RESOLVE_BUDGET = '2';
const seats: Record<string, unknown> = {};
for (let i = 0; i < 5; i++) {
  seats[`s${i}`] = { session_id: `0c0ffee0-0000-4000-8000-00000000000${i}`, tmux_session: `s${i}`, runtime: 'claude' };
}
writeFileSync(join(dir, 'state', 'agent-sessions.json'), JSON.stringify(seats));
process.env.ORCHESTRA_DIR = dir;
const { isTranscriptActive, resolveCallsForTest, RESOLVE_BUDGET_FOR_TEST } = await import('./transcript-activity.js');

test('one poll runs the full resolver at most RESOLVE_BUDGET times; the rest read unknown', async () => {
  assert.equal(RESOLVE_BUDGET_FOR_TEST, 2);
  for (const id of Object.keys(seats)) assert.equal(isTranscriptActive(id), false);
  assert.equal(resolveCallsForTest(), 2);
  await new Promise((r) => setTimeout(r, 1_100));         // the next window
  for (const id of Object.keys(seats)) isTranscriptActive(id);
  assert.equal(resolveCallsForTest(), 4, 'over-budget seats are not cached as misses: they resolve later');
});
