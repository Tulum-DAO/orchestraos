import { test } from 'node:test';
import assert from 'node:assert/strict';
import { panelStatus, pathTo, factsValue, activityValue, DRAWER_SECTIONS, liveStatus, computerValue, openingSection, contextUsedLabel } from './arturoPanel.ts';
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

test('the drawer: History, Library, Setup; it opens on History when something waits or runs, else where you left it', () => {
  assert.deepEqual(DRAWER_SECTIONS, ['history', 'library', 'setup']);
  assert.equal(openingSection('working', false, 'setup'), 'history');
  assert.equal(openingSection('idle', true, 'setup'), 'history');
  assert.equal(openingSection('needs_input', false, 'library'), 'history');
  assert.equal(openingSection('idle', false, 'library'), 'library');
  assert.equal(openingSection('idle', false, 'now'), 'history', 'a section that no longer exists');
  assert.equal(openingSection(undefined, false, null), 'history');
});

test('the context meter', () => {
  assert.equal(contextUsedLabel(38), '62% context used');
  assert.equal(contextUsedLabel(null), null);
  assert.equal(contextUsedLabel(140), null);
  assert.equal(contextUsedLabel(100), null, '0% used is the status line before its first reply: no reading');
});

test('the live status line in the header', () => {
  const now = 10_000_000;
  assert.equal(liveStatus('working', now - 3 * 60_000, now), 'Working · 3m');
  assert.equal(liveStatus('working', now - 20_000, now), 'Working · under a minute');
  assert.equal(liveStatus('working', now - 80 * 60_000, now), 'Working · 1h 20m');
  assert.equal(liveStatus('needs_input', now, now), 'Waiting for you');
  assert.equal(liveStatus('idle', now, now), 'Idle');
  assert.equal(liveStatus(undefined, undefined, now), 'Idle');
  assert.equal(liveStatus('failed', now, now), 'Stopped with an error');
  assert.equal(computerValue('working'), 'Working');
  assert.equal(computerValue('idle'), 'Idle');
  assert.equal(computerValue('needs_input'), 'Waiting');
});
