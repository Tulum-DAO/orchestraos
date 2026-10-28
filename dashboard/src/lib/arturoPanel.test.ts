import { test } from 'node:test';
import assert from 'node:assert/strict';
import { panelStatus, pathTo, factsValue, activityValue } from './arturoPanel.ts';
import type { ArturoJob } from './arturoJobs.ts';

const LIVE = { ok: true, live: true, live_engine: 'relay' as const };

test('the status line: ready with the brain when all is well, nothing to fix', () => {
  assert.deepEqual(panelStatus(LIVE, 'Claude CLI'), { text: 'Ready · Claude CLI', fix: null });
  assert.deepEqual(panelStatus(LIVE, ''), { text: 'Ready', fix: null });
  assert.deepEqual(panelStatus(null, 'Claude CLI'), { text: 'Checking…', fix: null });
  assert.deepEqual(panelStatus({ ok: false, status: 503 }, 'x'), { text: 'Starting…', fix: null });
});

test('the status line names what is wrong and jumps to the page that fixes it', () => {
  const brainDown = panelStatus({ ok: false, status: 500, error: 'boom' }, 'x');
  assert.deepEqual(brainDown, { text: "Arturo's brain is offline", fix: 'brain' });
  assert.deepEqual(pathTo(brainDown.fix!), ['settings', 'brain']);   // back from Brain goes to Settings
  assert.equal(panelStatus({ ok: false, error: 'arturo_disabled' }, 'x').fix, 'brain');
  assert.deepEqual(panelStatus({ ok: true, live: true, brain: { kind: 'none', model: '' } }, 'x'), { text: 'No brain connected', fix: 'brain' });
  const noVoice = panelStatus({ ok: true, live: false }, 'Claude CLI');
  assert.deepEqual(noVoice, { text: 'Ready · Claude CLI · Live voice not set up', fix: 'voice' });
  assert.deepEqual(pathTo(noVoice.fix!), ['voice']);
});

test('Memory row: the fact count', () => {
  assert.equal(factsValue(12), '12 facts');
  assert.equal(factsValue(1), '1 fact');
  assert.equal(factsValue(0), '0 facts');
  assert.equal(factsValue(null), '');
});

test('Activity row: running jobs, or Idle', () => {
  const job = (status: ArturoJob['status']) => ({ id: status, status }) as ArturoJob;
  assert.equal(activityValue([job('running'), job('running'), job('done')]), '2 running');
  assert.equal(activityValue([job('done'), job('failed')]), 'Idle');
  assert.equal(activityValue([]), 'Idle');
  assert.equal(activityValue(null), '');
});
