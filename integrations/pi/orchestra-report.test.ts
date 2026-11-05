import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { install, reportPath } from './orchestra-report.ts';

/** A stand-in for Pi: it records the handlers and fires them the way Pi's lifecycle does. */
function fakePi() {
  const handlers: Record<string, (ev: any, ctx: any) => unknown> = {};
  return {
    on(event: string, h: (ev: any, ctx: any) => unknown) { handlers[event] = h; },
    fire(event: string, ev: any = {}, ctx: any = {}) { handlers[event]?.(ev, ctx); },
    events: () => Object.keys(handlers),
  };
}

const ctx = (over: Record<string, unknown> = {}) => ({
  sessionManager: {
    getSessionId: () => 'sample-session',
    getSessionFile: () => '/srv/example/.pi/session.jsonl',
    getCwd: () => '/srv/example',
  },
  getContextUsage: () => ({ tokens: 41000, contextWindow: 200000, percent: 20.5 }),
  model: { provider: 'example', id: 'sample-1' },
  isIdle: () => true,
  ...over,
});

function setup() {
  const dataDir = mkdtempSync(join(tmpdir(), 'pi-report-'));
  const pi = fakePi();
  let t = 100;
  install(pi, { pane: '%12', dataDir, now: () => t++ });
  const read = () => JSON.parse(readFileSync(join(dataDir, 'state', 'agent-events', 'panes', '12.json'), 'utf8'));
  return { pi, dataDir, read };
}

test('the pane state file is named after the tmux pane, and nothing outside tmux', () => {
  assert.equal(reportPath({ pane: '%12', dataDir: '/d' }), '/d/state/agent-events/panes/12.json');
  assert.equal(reportPath({ pane: undefined, dataDir: '/d' }), null);
  assert.equal(reportPath({ pane: '../x', dataDir: '/d' }), null);
  const pi = fakePi();
  install(pi, { pane: undefined, dataDir: '/d' });
  assert.deepEqual(pi.events(), []);
});

test('a prompt reads working, its tools are named, and it ends idle', () => {
  const { pi, read } = setup();
  pi.fire('session_start', {}, ctx());
  assert.equal(read().state, 'idle');
  pi.fire('agent_start', {}, ctx());
  assert.equal(read().state, 'working');
  pi.fire('tool_execution_start', { toolName: 'bash' }, ctx());
  assert.deepEqual([read().state, read().tool], ['working', 'bash']);
  pi.fire('agent_end', {}, ctx());
  const done = read();
  assert.deepEqual([done.state, done.tool, done.event], ['idle', '', 'agent_end']);
});

test('the report carries what OrchestraOS reads: session, transcript, model and context', () => {
  const { pi, read } = setup();
  pi.fire('turn_end', {}, ctx());
  const r = read();
  assert.equal(r.pane, '%12');
  assert.equal(r.runtime, 'pi');
  assert.equal(r.session_id, 'sample-session');
  assert.equal(r.transcript_path, '/srv/example/.pi/session.jsonl');
  assert.equal(r.cwd, '/srv/example');
  assert.equal(r.model, 'example/sample-1');
  assert.deepEqual(r.context, { tokens: 41000, window: 200000, percent: 20.5 });
  assert.equal(typeof r.ts, 'number');
});

test('a model switch is reported with the new model', () => {
  const { pi, read } = setup();
  pi.fire('model_select', { model: { provider: 'example', id: 'sample-2' } }, ctx());
  assert.equal(read().model, 'example/sample-2');
  assert.equal(read().state, 'idle');
});

test('exit reads stopped', () => {
  const { pi, read } = setup();
  pi.fire('agent_start', {}, ctx());
  pi.fire('session_shutdown', {}, ctx());
  assert.equal(read().state, 'stopped');
});

test('a context with no session or usage still reports state, and nothing throws', () => {
  const { pi, read } = setup();
  const broken = { sessionManager: { getSessionId: () => { throw new Error('x'); } }, getContextUsage: () => { throw new Error('y'); } };
  assert.doesNotThrow(() => pi.fire('agent_start', {}, broken));
  const r = read();
  assert.equal(r.state, 'working');
  assert.equal(r.session_id, undefined);
  assert.equal(r.context, undefined);
});

test('writes are atomic: no temporary file is left behind', () => {
  const { pi, dataDir } = setup();
  pi.fire('agent_start', {}, ctx());
  const dir = join(dataDir, 'state', 'agent-events', 'panes');
  assert.deepEqual(readdirSync(dir), ['12.json']);
  assert.ok(existsSync(join(dir, '12.json')));
});
