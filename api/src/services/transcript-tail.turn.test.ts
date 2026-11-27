/**
 * The tailer's turn marks (r26, gm msg_8da9ef1f): as a log in the shape of gm's grows line by line, turn() says when the turn ended
 * and which lines are final replies, the same as live-step reads them from the whole file, and the step beat's repeat
 * reads of an unchanged log give the same answer.
 *
 * Run: npx tsx --test src/services/transcript-tail.turn.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from '../lib/testTmp.js';
import { TranscriptTailer } from './transcript-tail.js';
import { finalReplyUuids, turnEndFromLines } from './live-step.js';

const LINES = readFileSync(new URL('./fixtures/turn-end.jsonl', import.meta.url), 'utf-8').split('\n').filter(Boolean);

test('turn() follows the log as it grows: ended at each final reply, reopened by the Stop hook, ended again', () => {
  const f = join(testTmpDir('tail-turn-'), 's.jsonl');
  writeFileSync(f, LINES[0] + '\n');
  const t = new TranscriptTailer({ agentId: 'gm', resolve: () => ({ path: f, sid: 's' }) });
  t.subscribe(() => {});
  const seen: Array<string | null> = [];
  for (let k = 1; k < LINES.length; k++) {
    appendFileSync(f, LINES[k] + '\n');
    t.tick();
    const turn = t.turn();
    const want = LINES.slice(0, k + 1);
    assert.equal(turn.ended, turnEndFromLines(want), `line ${k}`);
    assert.deepEqual([...turn.finals].sort(), [...finalReplyUuids(want)].sort(), `line ${k}`);
    assert.equal(t.turn(), turn, 'an unchanged log is not parsed again');
    if (seen[seen.length - 1] !== turn.ended) seen.push(turn.ended);
  }
  // the one-turn answer, its turn_duration, the next message, the reopened "Seen it", the final reply, its turn_duration
  assert.deepEqual(seen.map((s) => s && s.slice(11, 23)),
    [null, '09:40:41.000', '09:40:46.000', null, '09:41:47.000', null, '09:42:30.000', '09:42:34.000']);
});
