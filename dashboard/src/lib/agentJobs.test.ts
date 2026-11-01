import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fetchAgentJobs, jobElapsed, trayJobs, type AgentJob } from './agentJobs.ts';

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
    { id: 'd', kind: 'command', title: 'lint', status: 'failed', started_at: '2026-01-01T10:00:00Z' },
  ];
  assert.deepEqual(trayJobs(jobs).map((j) => j.id), ['a', 'b', 'd']);
  assert.deepEqual(trayJobs(jobs, new Set(['d'])).map((j) => j.id), ['a', 'b']);
  assert.equal(jobElapsed(jobs[1], Date.parse('2026-01-01T10:01:42Z')), '0:42');
  assert.equal(jobElapsed({ ...jobs[1], ended_at: '2026-01-01T11:13:05Z' }), '1:12:05');
  assert.equal(jobElapsed({ ...jobs[1], started_at: '' }), '');
});
