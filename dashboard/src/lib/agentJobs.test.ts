import { test } from 'node:test';
import assert from 'node:assert/strict';
import { failedLabel, fetchAgentJobs, FAILED_SHOWN_MS, jobElapsed, trayJobs, type AgentJob } from './agentJobs.ts';

const answer = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;

test('jobs: a 404 is "no tray yet", any other failure keeps what is shown, rows are checked', async () => {
  assert.equal(await fetchAgentJobs('dev-demo', answer(404, { error: 'not found' })), 'none');
  assert.equal(await fetchAgentJobs('dev-demo', answer(500, {})), null);
  assert.equal(await fetchAgentJobs('dev-demo', (async () => { throw new Error('offline'); }) as typeof fetch), null);
  let url = '';
  const got = await fetchAgentJobs('dev-demo', (async (u: string) => { url = u; return new Response(JSON.stringify({ ok: true, jobs: [
    { id: 'j1', kind: 'command', title: 'npm test', status: 'running', started_at: '2026-01-01T10:00:00Z' },
    { id: 'j2', kind: 'subagent', title: '', status: 'failed', started_at: '2026-01-01T10:00:00Z', ended_at: '2026-01-01T10:01:00Z', result_uuid: 'u-9' },
    { id: 'j3', kind: 'mystery', title: 'x', status: 'running', started_at: '' },
  ] }), { status: 200 }); }) as typeof fetch);
  assert.equal(url, '/api/agents/dev-demo/jobs');
  assert.ok(Array.isArray(got));
  assert.deepEqual((got as AgentJob[]).map((j) => [j.id, j.title, j.result_uuid ?? null]), [['j1', 'npm test', null], ['j2', 'Subagent', 'u-9']]);
});

test('the tray: running work oldest first, then failures until dismissed; done work is in the thread', () => {
  const jobs: AgentJob[] = [
    { id: 'b', kind: 'subagent', title: 'Sample review', status: 'running', started_at: '2026-01-01T10:02:00Z' },
    { id: 'a', kind: 'command', title: 'npm test', status: 'running', started_at: '2026-01-01T10:01:00Z' },
    { id: 'c', kind: 'wait', title: 'Waiting for the build', status: 'done', started_at: '2026-01-01T10:00:00Z', result_uuid: 'u-1' },
    { id: 'd', kind: 'command', title: 'lint', status: 'failed', started_at: '2026-01-01T10:00:00Z', ended_at: '2026-01-01T10:00:30Z' },
  ];
  const at = Date.parse('2026-01-01T10:05:00Z');
  assert.deepEqual(trayJobs(jobs, new Set(), at).map((j) => j.id), ['a', 'b', 'd']);
  assert.deepEqual(trayJobs(jobs, new Set(['d']), at).map((j) => j.id), ['a', 'b']);
  assert.equal(jobElapsed(jobs[1], Date.parse('2026-01-01T10:01:42Z')), '0:42');
  assert.equal(jobElapsed({ ...jobs[1], ended_at: '2026-01-01T11:13:05Z' }), '1:12:05');
  assert.equal(jobElapsed({ ...jobs[1], started_at: '' }), '');
});

test('a failed chip says how it ended (the server\'s reason), else "failed"', async () => {
  const got = await fetchAgentJobs('dev-demo', (async () => new Response(JSON.stringify({ ok: true, jobs: [
    { id: 'f1', kind: 'command', title: 'npm test', status: 'failed', started_at: '2026-01-01T10:00:00Z', ended_at: '2026-01-01T10:01:00Z', reason: ' exited with an error ' },
    { id: 'f2', kind: 'subagent', title: 'Sample review', status: 'failed', started_at: '2026-01-01T10:00:00Z', ended_at: '2026-01-01T10:01:00Z', reason: '' },
    { id: 'f3', kind: 'wait', title: 'Waiting', status: 'failed', started_at: '2026-01-01T10:00:00Z', ended_at: '2026-01-01T10:01:00Z' },
  ] }))) as typeof fetch) as AgentJob[];
  assert.deepEqual(got.map(failedLabel), ['exited with an error', 'failed', 'failed']);
});

test('failures older than an hour (or with no readable end) never show, whatever an older API sends; running work always does', () => {
  const now = Date.parse('2026-01-06T12:00:00Z');
  const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
  const jobs: AgentJob[] = [
    { id: 'old', kind: 'command', title: 'Sleep for 25 seconds', status: 'failed', started_at: iso(5 * 864e5 + 25e3), ended_at: iso(5 * 864e5), reason: 'was stopped' },
    { id: 'edge', kind: 'command', title: 'lint', status: 'failed', started_at: iso(FAILED_SHOWN_MS + 5e3), ended_at: iso(FAILED_SHOWN_MS) },
    { id: 'late', kind: 'command', title: 'build', status: 'failed', started_at: iso(FAILED_SHOWN_MS + 2e3), ended_at: iso(FAILED_SHOWN_MS + 1e3) },
    { id: 'noend', kind: 'command', title: 'test', status: 'failed', started_at: iso(1e3) },
    { id: 'new', kind: 'command', title: 'npm test', status: 'failed', started_at: iso(120e3), ended_at: iso(60e3), reason: 'exited with an error' },
    { id: 'run', kind: 'subagent', title: 'Sample review', status: 'running', started_at: iso(3 * 3600e3) },
  ];
  assert.deepEqual(trayJobs(jobs, new Set(), now).map((j) => j.id), ['run', 'edge', 'new']);
  // dismiss still takes a recent failure away
  assert.deepEqual(trayJobs(jobs, new Set(['new']), now).map((j) => j.id), ['run', 'edge']);
});
