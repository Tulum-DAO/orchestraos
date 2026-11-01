/**
 * An agent's job tray (GET /api/agents/:id/jobs, lib/agentJobs.ts) from fixture Claude Code transcripts and a fixture
 * supervisor log: background commands, subagents and waits, running until the line that ends them, with result_uuid
 * on the line the chat already shows; a headless CLI's exit ends what was still running; a subagent's own lines are
 * never jobs; a non-Claude seat has none.
 *
 * Run: npx tsx --test src/lib/agentJobs.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseJobs, endWithCli, lastCliExit, trayJobs, REASON_ERROR, REASON_STOPPED, REASON_RESTARTED, REASON_SUBAGENT_ERROR } from './agentJobs.js';
import { createAgentJobsRouter } from '../routes/agent-jobs.js';

const at = (m: string) => `2026-11-01T10:${m}Z`;
const use = (uuid: string, ts: string, id: string, name: string, input: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  JSON.stringify({ type: 'assistant', uuid, timestamp: at(ts), ...extra, message: { role: 'assistant', content: [{ type: 'tool_use', id, name, input }] } });
const result = (uuid: string, ts: string, id: string, text: string, isError = false) =>
  JSON.stringify({ type: 'user', uuid, timestamp: at(ts), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content: text, is_error: isError }] } });
const noticeText = (id: string, status: string) => `<task-notification>\n<task-id>x</task-id>\n<tool-use-id>${id}</tool-use-id>\n<status>${status}</status>\n</task-notification>`;
const noticeMidTurn = (uuid: string, ts: string, id: string, status: string) =>
  JSON.stringify({ type: 'attachment', uuid, timestamp: at(ts), attachment: { type: 'queued_command', prompt: noticeText(id, status), origin: { kind: 'task-notification' } } });
const noticeLine = (uuid: string, ts: string, id: string, status: string) =>
  JSON.stringify({ type: 'user', uuid, timestamp: at(ts), message: { role: 'user', content: noticeText(id, status) } });

const LOG = [
  JSON.stringify({ type: 'user', uuid: 'u0', timestamp: at('00:00'), message: { role: 'user', content: 'run the sample build' } }),
  // A background command that finishes mid-turn, one that fails, one still running.
  use('a1', '00:01', 'bg1', 'Bash', { command: 'npm run build', description: 'Build the sample', run_in_background: true }),
  result('r1', '00:01', 'bg1', 'Command running in background with ID: bash_1'),
  use('a2', '00:02', 'bg2', 'Bash', { command: 'npm test', run_in_background: true }),
  result('r2', '00:02', 'bg2', 'Command running in background with ID: bash_2'),
  use('a3', '00:03', 'bg3', 'Bash', { command: 'tail -f example.log', description: 'Follow the log', run_in_background: true }),
  result('r3', '00:03', 'bg3', 'Command running in background with ID: bash_3'),
  noticeMidTurn('n1', '00:10', 'bg1', 'completed'),
  noticeLine('n2', '00:11', 'bg2', 'failed'),
  // A foreground subagent (ends with its result), an async one (ends with its notification), one still running.
  use('a4', '00:12', 'sa1', 'Task', { description: 'Review the sample diff', subagent_type: 'general-purpose' }),
  // The subagent's own tool calls are its work, not the agent's jobs.
  use('s1', '00:13', 'side1', 'Bash', { command: 'sleep 5' }, { isSidechain: true }),
  result('r4', '00:20', 'sa1', 'The diff looks fine.'),
  use('a5', '00:21', 'sa2', 'Agent', { description: 'Explore the example repo' }),
  result('r5', '00:21', 'sa2', 'Async agent launched successfully. agentId: x'),
  noticeLine('n3', '00:30', 'sa2', 'completed'),
  use('a6', '00:31', 'sa3', 'Task', { description: 'Write the sample report' }),
  // Waits: a foreground sleep (done), a poll loop (running), a wakeup (its delay decides), a Monitor (running).
  use('a7', '00:32', 'w1', 'Bash', { command: 'sleep 30' }),
  result('r7', '00:33', 'w1', ''),
  use('a8', '00:34', 'w2', 'Bash', { command: 'until [ -f done.flag ]; do sleep 2; done' }),
  use('a9', '00:35', 'w3', 'ScheduleWakeup', { delaySeconds: 600, reason: 'Check the sample deploy' }),
  result('r9', '00:35', 'w3', 'Scheduled.'),
  use('a10', '00:36', 'w4', 'Monitor', { description: 'Watch for the build to finish' }),
  // Ordinary work is not a job.
  use('a11', '00:37', 'plain', 'Read', { file_path: '/repo/a' }),
  result('r11', '00:37', 'plain', 'contents'),
];

const byId = (jobs: ReturnType<typeof parseJobs>) => Object.fromEntries(jobs.map((j) => [j.id, j]));

test('background commands: running until their notification, which is where the result card goes', () => {
  const j = byId(parseJobs(LOG, Date.parse(at('01:00'))));
  assert.deepEqual(j.bg1, { id: 'bg1', kind: 'command', title: 'Build the sample', status: 'done',
    started_at: at('00:01.000'), ended_at: at('00:10.000'), result_uuid: 'n1', tool_use_id: 'bg1', launch_uuid: 'a1' });
  assert.deepEqual([j.bg2.status, j.bg2.result_uuid, j.bg2.title], ['failed', 'n2', 'npm test']);
  assert.deepEqual([j.bg3.status, j.bg3.ended_at, j.bg3.result_uuid], ['running', undefined, undefined]);
});

test('subagents: one job each, foreground or async; their own lines are never jobs', () => {
  const j = byId(parseJobs(LOG, Date.parse(at('01:00'))));
  assert.deepEqual([j.sa1.kind, j.sa1.status, j.sa1.result_uuid, j.sa1.title], ['subagent', 'done', 'r4', 'Review the sample diff']);
  assert.deepEqual([j.sa2.status, j.sa2.result_uuid], ['done', 'n3'], 'async: its notification, not "launched"');
  assert.equal(j.sa3.status, 'running');
  assert.equal(j.side1, undefined);
  assert.equal(j.plain, undefined);
});

test('waits: a sleep, a poll loop, a wakeup out its delay, a monitor', () => {
  const before = byId(parseJobs(LOG, Date.parse(at('01:00'))));
  assert.deepEqual([before.w1.kind, before.w1.status, before.w1.result_uuid], ['wait', 'done', 'r7']);
  assert.deepEqual([before.w2.kind, before.w2.status], ['wait', 'running']);
  assert.deepEqual([before.w3.kind, before.w3.status, before.w3.title], ['wait', 'running', 'Check the sample deploy']);
  assert.deepEqual([before.w4.kind, before.w4.status], ['wait', 'running']);
  const after = byId(parseJobs(LOG, Date.parse(at('45:36'))));
  assert.deepEqual([after.w3.status, after.w3.ended_at], ['done', at('10:35.000')]);
});

test('a headless CLI that exited ends what was still running in it; later jobs stay running', () => {
  const jobs = endWithCli(parseJobs(LOG, Date.parse(at('01:00'))), at('00:31.500'));
  const j = byId(jobs);
  assert.deepEqual([j.bg3.status, j.bg3.ended_at], ['failed', at('00:31.500')]);
  assert.equal(j.sa3.status, 'failed');
  assert.equal(j.w2.status, 'running', 'started after the exit');
  assert.equal(j.bg1.status, 'done', 'a finished job keeps its own end');
});

test('the supervisor log\'s last CLI exit, from its tail', () => {
  const d = mkdtempSync(join(tmpdir(), 'agent-jobs-'));
  const f = join(d, 'events.jsonl');
  writeFileSync(f, [
    JSON.stringify({ kind: 'sup', what: 'start', wall: at('00:00') }),
    JSON.stringify({ kind: 'sup', what: 'exit', wall: at('00:05'), code: 0 }),
    JSON.stringify({ kind: 'out', ev: { type: 'assistant', message: { content: [{ type: 'text', text: '"exit"' }] } } }),
    JSON.stringify({ kind: 'sup', what: 'exit', wall: at('00:20'), code: 1 }),
    JSON.stringify({ kind: 'sup', what: 'start', wall: at('00:21') }),
  ].join('\n') + '\n');
  assert.equal(lastCliExit(f), at('00:20.000'));
  assert.equal(lastCliExit(join(d, 'missing.jsonl')), null);
});

test('the tray: running first, then the newest finished, capped', () => {
  const t = trayJobs(parseJobs(LOG, Date.parse(at('01:00'))), 2, Date.parse(at('01:00')));
  assert.deepEqual(t.map((j) => j.id), ['w4', 'w3', 'w2', 'sa3', 'bg3', 'w1', 'sa2']);
});

test('GET /api/agents/:id/jobs: headless and tmux seats from the transcript, none for another runtime', async () => {
  const d = mkdtempSync(join(tmpdir(), 'agent-jobs-route-'));
  const log = join(d, 'session.jsonl');
  writeFileSync(log, LOG.join('\n') + '\n');
  const events = join(d, 'events.jsonl');
  writeFileSync(events, JSON.stringify({ kind: 'sup', what: 'exit', wall: at('00:31.500') }) + '\n');
  const router = createAgentJobsRouter({
    runtime: (id) => (id === 'pm-demo' ? 'codex' : 'claude'),
    transcriptPath: (id) => (id === 'qa-demo' ? null : log),
    headless: (id) => id === 'dev-demo',
    eventsFile: () => events,
    now: () => Date.parse(at('01:00')),
  });
  const app = express();
  app.use('/api/agents', router);
  const server = app.listen(0);
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/agents`;
  try {
    const get = async (id: string) => (await fetch(`${base}/${id}/jobs`)).json() as Promise<any>;
    const headless = await get('dev-demo');
    assert.equal(headless.ok, true);
    assert.equal(headless.source, 'transcript');
    assert.equal(headless.jobs.find((j: any) => j.id === 'bg3').status, 'failed', 'ended with the CLI');
    const tmux = await get('sample-seat');
    assert.equal(tmux.jobs.find((j: any) => j.id === 'bg3').status, 'running', 'a tmux seat: the transcript alone');
    assert.deepEqual(Object.keys(tmux.jobs[0]).sort(), ['id', 'kind', 'launch_uuid', 'started_at', 'status', 'title', 'tool_use_id']);
    assert.deepEqual(await get('pm-demo'), { ok: true, agent: 'pm-demo', jobs: [], source: 'not_claude' });
    assert.deepEqual(await get('qa-demo'), { ok: true, agent: 'qa-demo', jobs: [], source: 'no_transcript' });
    assert.equal((await fetch(`${base}/bad%20id/jobs`)).status, 400);
  } finally { server.close(); }
});

test('every result_uuid is a row the chat already shows: the notification line or the tool result', async () => {
  const { normalizeTranscript } = await import('../routes/chat-transcript.js');
  const env = normalizeTranscript(LOG, 'dev-demo', 'sample-session', 500, false, true);
  const uuids = new Set(env.items.map((i: any) => i.uuid));
  for (const j of parseJobs(LOG, Date.parse(at('01:00'))).filter((x) => x.result_uuid)) {
    assert.ok(uuids.has(j.result_uuid), `${j.id} -> ${j.result_uuid}`);
  }
  const n1 = env.items.find((i: any) => i.uuid === 'n1');
  assert.equal(n1.machine, 'task', 'a finished command is the chat\'s finished-work row');
});

// The live report: a foreground `sleep 25` the CLI refused at once, shown five days later as "didn't finish".
const refused = (uuid: string, ts: string, id: string, text: string) =>
  JSON.stringify({ type: 'user', uuid, timestamp: at(ts), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, is_error: true,
    content: [{ type: 'text', text }] }] } });
const REFUSALS = [
  use('x1', '00:00', 'sleep25', 'Bash', { command: 'sleep 25' }),
  refused('y1', '00:00', 'sleep25', '<tool_use_error>Blocked: standalone sleep 25 is not allowed</tool_use_error>'),
  // A refused background command and a refused subagent: never ran either.
  use('x2', '00:01', 'bgref', 'Bash', { command: 'npm run sample', run_in_background: true }),
  refused('y2', '00:01', 'bgref', '<tool_use_error>Permission denied</tool_use_error>'),
  use('x3', '00:02', 'saref', 'Task', { description: 'Sample review' }),
  JSON.stringify({ type: 'user', uuid: 'y3', timestamp: at('00:02.4'), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'saref', is_error: true, content: 'The user doesn\'t want to proceed with this tool use.' }] } }),
  // Ran, then failed: a subagent that errored after a minute, a foreground wait that was interrupted.
  use('x4', '00:03', 'saran', 'Task', { description: 'Write the example report' }),
  refused('y4', '01:03', 'saran', 'Agent ran into an API error after 12 steps'),
  use('x5', '00:04', 'waitrun', 'Bash', { command: 'sleep 120' }),
  refused('y5', '00:30', 'waitrun', '[Request interrupted by user for tool use]'),
];
test('a call the CLI refused at once is never a job, foreground, background or subagent', () => {
  const j = byId(parseJobs(REFUSALS, Date.parse(at('02:00'))));
  assert.equal(j.sleep25, undefined, 'the refused sleep');
  assert.equal(j.bgref, undefined);
  assert.equal(j.saref, undefined, 'an error within a second is a refusal too');
});

test('a job that ran and then failed is failed, with a plain reason', () => {
  const j = byId(parseJobs([...REFUSALS, ...LOG], Date.parse(at('02:00'))));
  assert.deepEqual([j.saran.status, j.saran.reason, j.saran.result_uuid], ['failed', REASON_SUBAGENT_ERROR, 'y4']);
  assert.deepEqual([j.waitrun.status, j.waitrun.reason], ['failed', REASON_STOPPED]);
  assert.deepEqual([j.bg2.status, j.bg2.reason], ['failed', REASON_ERROR], 'a background command whose notification says failed');
  assert.equal(j.bg1.reason, undefined, 'a done job has no reason');
  const restarted = byId(endWithCli(parseJobs(LOG, Date.parse(at('01:00'))), at('00:31.500')));
  assert.equal(restarted.bg3.reason, REASON_RESTARTED);
  const killed = byId(parseJobs([
    use('k1', '00:00', 'kill1', 'Bash', { command: 'npm run watch', run_in_background: true }),
    result('k2', '00:00', 'kill1', 'Command running in background with ID: bash_9'),
    noticeLine('k3', '00:05', 'kill1', 'killed'),
  ]));
  assert.deepEqual([killed.kill1.status, killed.kill1.reason], ['failed', REASON_STOPPED]);
});

test('finished jobs only within the last hour; running jobs always', () => {
  const jobs = parseJobs([...REFUSALS, ...LOG], Date.parse(at('02:00')));
  const soon = trayJobs(jobs, 20, Date.parse(at('30:00')));
  assert.ok(soon.some((j) => j.id === 'saran' && j.reason === REASON_SUBAGENT_ERROR), 'a recent real failure, with its reason');
  const later = trayJobs(jobs, 20, Date.parse('2026-11-01T12:00:00Z'));
  assert.ok(!later.some((j) => j.status !== 'running'), 'an old failure (and every old finished job) is gone');
  const running = jobs.filter((j) => j.status === 'running').map((j) => j.id).sort();
  assert.deepEqual(later.map((j) => j.id).sort(), running, 'every running job stays, however old');
});

test('every job names its launching tool call, and the thread has that call on that row', async () => {
  const { normalizeTranscript } = await import('../routes/chat-transcript.js');
  const env = normalizeTranscript(LOG, 'dev-demo', 'sample-session', 500, false, true);
  const jobs = parseJobs(LOG, Date.parse(at('01:00')));
  assert.ok(jobs.length >= 10);
  for (const j of jobs) {
    assert.equal(j.tool_use_id, j.id);
    assert.ok(j.launch_uuid, `${j.id} has a launch row`);
    const item = env.items.find((i: any) => i.kind === 'tool_use' && i.id === j.tool_use_id);
    assert.ok(item, `${j.id}: its tool call is in the items`);
    assert.equal(item.uuid, j.launch_uuid, `${j.id}: on that row`);
    assert.ok(env.render_items.some((n: any) => n.kind === 'tool' && String(n.key).startsWith(`${j.launch_uuid}:`)),
      `${j.id}: the tool card is keyed by that row`);
  }
});
