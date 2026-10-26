import { test } from 'node:test';
import assert from 'node:assert';
import { elapsed, jobResultParts, jobToolParts, newlyEnded, trayHeader, type ArturoJob } from './arturoJobs.ts';

const job = (over: Partial<ArturoJob>): ArturoJob => ({
  id: 'job_a', thread: 't', kind: 'research', title: 'Reading the post', status: 'running',
  started: 100, ended: null, steps: [], result: null, error: null, ...over,
});

test('the tray header names the latest running job and its clock', () => {
  assert.equal(trayHeader([job({}), job({ id: 'job_b', title: 'Asking about deploys', started: 130 })], 142),
    '2 running · Asking about deploys · 0:12');
  assert.equal(trayHeader([job({ status: 'failed', ended: 120 })], 200), "1 didn't finish · Reading the post");
  assert.equal(trayHeader([job({ status: 'done', ended: 120 })], 200), '');
  assert.equal(elapsed(job({ started: 0 }), 75), '1:15');
});

test('steps become tool rows; an unfinished step of an ended job reads failed', () => {
  const rows = jobToolParts(job({ status: 'failed', steps: [
    { call_id: 'a', name: 'WebSearch', args_summary: '{}', ok: true, summary: '3 results' },
    { call_id: null, name: 'WebFetch', args_summary: '{}', ok: null, summary: '' }] }));
  assert.deepEqual(rows.map((r) => [r.name, r.status]), [['WebSearch', 'ok'], ['WebFetch', 'failed']]);
  assert.equal(rows[1].callId, 'job_a-1');
  assert.equal(jobToolParts(job({ steps: [{ call_id: 'x', name: 'WebFetch', args_summary: '', ok: null, summary: '' }] }))[0].status, 'running');
});

test('a job that was running and now is not has ended (its result is in the thread)', () => {
  const before = [job({}), job({ id: 'job_b' })];
  const after = [job({ status: 'done', ended: 150 }), job({ id: 'job_b' }), job({ id: 'job_c', status: 'done' })];
  assert.deepEqual(newlyEnded(before, after), ['job_a']);
});

test('a finished job un-pins into the thread: its steps, then its answer', () => {
  const parts = jobResultParts({ kind: 'job_result', job_id: 'job_z', steps: [
    { call_id: 'd1', name: 'list_agents', args_summary: '{}', ok: true, summary: '3 running' }] }, 'All clear.');
  assert.deepEqual(parts?.map((p) => p.kind), ['tool', 'text']);
  assert.equal(parts?.[0].kind === 'tool' && parts[0].status, 'ok');
  assert.equal(jobResultParts({ kind: 'job_result', steps: [] }, 'x'), undefined);
  assert.equal(jobResultParts(undefined, 'x'), undefined);
});
