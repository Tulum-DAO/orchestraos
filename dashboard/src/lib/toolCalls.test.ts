import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fromBlock, fromPart, callLabel, iconKey, callDetail, hasDetail, runSummary, callRunSummary, callCode, callLine, liveCall, runTimes } from './toolCalls.ts';
import type { ToolBlock } from './transcript.ts';

const block = (over: Partial<ToolBlock>): ToolBlock =>
  ({ kind: 'tool', tool: 'Bash', input: { command: 'ls' }, key: 'k1', ...over }) as ToolBlock;

test('an agent transcript call: status from its result, its file, its todos', () => {
  assert.equal(fromBlock(block({})).status, 'running');
  assert.equal(fromBlock(block({ result: 'a\nb' })).status, 'ok');
  assert.equal(fromBlock(block({ result: 'boom', isError: true })).status, 'failed');
  const read = fromBlock(block({ tool: 'Read', input: { file_path: '/srv/sample/a.ts' }, result: 'x' }));
  assert.equal(read.filePath, '/srv/sample/a.ts');
  const todo = fromBlock(block({ tool: 'TodoWrite', input: { todos: [{ content: 'one', status: 'completed' }, { content: 'two', status: 'in_progress' }] }, result: 'ok' }));
  assert.deepEqual(todo.todos.map((t) => t.status), ['completed', 'in_progress']);
  assert.equal(todo.chip, '2 todos · 1 done');
});

test("Arturo's stream call: its summary words and status", () => {
  const p = fromPart({ kind: 'tool', callId: 'c1', name: 'list_agents', argsSummary: '{"scope":"all"}', status: 'ok', summary: '3 agents' });
  assert.equal(p.chip, 'all');
  assert.equal(p.result, '3 agents');
  assert.equal(p.arturo, true);
});

test('labels: Arturo in plain words, agent CLIs as named', () => {
  assert.equal(callLabel('list_agents'), 'List agents');
  assert.equal(callLabel('Bash'), 'Bash');
  assert.equal(callLabel('TodoWrite'), 'TodoWrite');
  assert.equal(callLabel('exec_command'), 'Exec command');
});

test('icons for both worlds, and a wrench for anything unknown', () => {
  assert.equal(iconKey('Bash'), 'terminal');
  assert.equal(iconKey('exec_command'), 'terminal');
  assert.equal(iconKey('Edit'), 'edit');
  assert.equal(iconKey('apply_patch'), 'edit');
  assert.equal(iconKey('WebFetch'), 'web');
  assert.equal(iconKey('spawn_agent'), 'agent');
  assert.equal(iconKey('TodoWrite'), 'todo');
  assert.equal(iconKey('something_new'), 'tool');
});

test("a row opens only when there is something to show; Arturo's results drop lines meant for the model", () => {
  assert.equal(hasDetail(fromBlock(block({}))), false);                     // still running
  assert.equal(hasDetail(fromBlock(block({ result: '' }))), false);         // nothing came back
  assert.equal(hasDetail(fromBlock(block({ result: 'out' }))), true);
  const d = callDetail(fromPart({ kind: 'tool', callId: 'c', name: 'read_file', argsSummary: '', status: 'ok',
    summary: 'Read 3 lines. Consider using gm_command next.' }));
  assert.deepEqual(d.resultLines, ['Read 3 lines.']);
  // a long input is shown when the chip cannot hold it, never for a command
  const big = { text: 'x'.repeat(300) };
  assert.ok(callDetail(fromBlock(block({ tool: 'Write', input: big, result: 'ok' }))).input);
  assert.equal(callDetail(fromBlock(block({ tool: 'Bash', input: { command: 'y'.repeat(300) }, result: 'ok' }))).input, null);
});

test('the run header says how long it worked and what it did', () => {
  const calls = [
    fromBlock(block({ tool: 'Edit', input: { file_path: '/a' }, result: 'ok', ts: '2026-10-29T10:00:00Z', resultTs: '2026-10-29T10:00:30Z', key: 'a' })),
    fromBlock(block({ tool: 'Bash', input: { command: 'ls' }, result: 'ok', ts: '2026-10-29T10:00:40Z', resultTs: '2026-10-29T10:01:08Z', key: 'b' })),
  ];
  const s = runSummary(calls);
  assert.equal(s.lead, 'Worked for 1m 8s');
  assert.match(s.headline, /edited 1 file/);
  assert.equal(s.tone, 'done');
  assert.equal(runSummary([fromBlock(block({ key: 'r' }))]).lead, 'Working');
});

// A voice call's card (gm, 2026-11-27): three tool rows under "used 2 tools, 1 failed" read as a contradiction, so a
// call card's header counts every row it opens to; the kit's other groups keep counting what worked.
test("a call card's tool header counts every row: used 3 tools, 1 failed", () => {
  const part = (callId: string, name: string, status: 'ok' | 'failed' | 'running') =>
    fromPart({ kind: 'tool', callId, name, argsSummary: '', status, summary: '' });
  const rows = [part('a', 'list_agents', 'ok'), part('b', 'gm_command', 'failed'), part('c', 'research', 'ok')];
  assert.equal(callRunSummary(rows).headline, 'used 3 tools, 1 failed');
  assert.equal(runSummary(rows).headline, 'used 2 tools, 1 failed');   // kit-wide count unchanged
  assert.equal(callRunSummary(rows).lead, 'Finished');                 // a failure: not "Worked" (below)
  assert.equal(callRunSummary([part('a', 'list_agents', 'ok'), part('b', 'research', 'ok')]).headline, 'used 2 tools');
  assert.equal(callRunSummary([part('a', 'gm_command', 'failed'), part('b', 'research', 'failed')]).headline, 'used 2 tools, 2 failed');
  assert.equal(callRunSummary([part('a', 'list_agents', 'ok'), part('b', 'research', 'running')]).headline, 'used 2 tools, 1 running');
  assert.equal(callRunSummary([part('a', 'list_agents', 'ok'), part('b', 'research', 'running')], true).headline, 'used 2 tools, 1 interrupted');
});

test("a call's code block (Bubbles' thin lines): its input as written and what came back, copied together", () => {
  const bash = fromBlock(block({ input: { command: 'ls -la /srv' }, result: 'a\nb' }));
  assert.deepEqual(callCode(bash), { input: '$ ls -la /srv', output: 'a\nb', copy: '$ ls -la /srv\n\na\nb' });
  const edit = fromBlock(block({ tool: 'Edit', input: { file_path: '/a.ts', old_string: 'x' }, result: 'ok' }));
  assert.equal(callCode(edit).input, JSON.stringify({ file_path: '/a.ts', old_string: 'x' }, null, 2));
  const todo = fromBlock(block({ tool: 'TodoWrite', input: { todos: [{ content: 'one', status: 'completed' }, { content: 'two', status: 'in_progress' }, { content: 'three', status: 'pending' }] }, result: '' }));
  assert.equal(callCode(todo).input, '✓ one\n◐ two\n○ three');
  assert.equal(callCode(todo).copy, '✓ one\n◐ two\n○ three');
  // Arturo's call has no input object: its argument words, and its result without sentences meant for the model
  const a = fromPart({ callId: 'c1', name: 'list_agents', status: 'ok', argsSummary: 'all', summary: '3 agents' } as never);
  assert.equal(callCode(a).input, a.chip);
  assert.equal(callCode(a).output, '3 agents');
  // an empty result: just the input
  assert.equal(callCode(fromBlock(block({ result: '' }))).copy, '$ ls');
});

test("a call's thin line: its name and a short summary (a file as its short path)", () => {
  assert.deepEqual(callLine(fromBlock(block({ input: { command: 'D=/home/orchestra/x; ls $D' }, result: '' }))), { label: 'Bash', detail: 'D=/home/orchestra/x; ls $D' });
  assert.deepEqual(callLine(fromBlock(block({ tool: 'Read', input: { file_path: '/srv/sample/deep/a.ts' }, result: 'x' }))).label, 'Read');
  assert.ok(callLine(fromBlock(block({ tool: 'Read', input: { file_path: '/srv/sample/deep/a.ts' }, result: 'x' }))).detail.endsWith('a.ts'));
});

test('the live step of a run: the call still going (the last one), else none', () => {
  const done = fromBlock(block({ key: 'k1', result: 'x' }));
  const going = fromBlock(block({ key: 'k2', input: { command: 'npm test' } }));
  assert.equal(liveCall([done, going])?.id, 'k2');
  assert.equal(liveCall([done]), undefined);
  // an interrupted run has nothing still going
  assert.equal(liveCall([done, going], true), undefined);
});

test("a run's times: when it started, and when it ended if it took more than a minute; a live run only its start", () => {
  const c = (key: string, ts?: string, resultTs?: string, running = false) =>
    fromBlock(block({ key, ...(ts ? { ts } : {}), ...(resultTs ? { resultTs } : {}), result: running ? undefined : 'x' }));
  assert.deepEqual(runTimes([c('a', '2026-11-02T10:12:00Z', '2026-11-02T10:12:20Z'), c('b', '2026-11-02T10:12:30Z', '2026-11-02T10:12:50Z')]),
    { start: '2026-11-02T10:12:00Z' });
  assert.deepEqual(runTimes([c('a', '2026-11-02T10:12:00Z', '2026-11-02T10:13:00Z'), c('b', '2026-11-02T10:16:00Z', '2026-11-02T10:17:05Z')]),
    { start: '2026-11-02T10:12:00Z', end: '2026-11-02T10:17:05Z' });
  // still going: the start only
  assert.deepEqual(runTimes([c('a', '2026-11-02T10:12:00Z', '2026-11-02T10:15:00Z'), c('b', '2026-11-02T10:16:00Z', undefined, true)]),
    { start: '2026-11-02T10:12:00Z' });
  // a runtime that gives no per-call times: none, never a guess
  assert.equal(runTimes([c('a'), c('b')]), null);
});

// gm on the live call card (2026-11-27): "Worked — used 3 tools, 1 failed" read as all-good. With any failure a call
// card's header says it finished, not that it worked; all ok keeps "Worked". The kit's other groups are unchanged.
test("a call card's header with a failure says Finished, not Worked; all ok keeps Worked", () => {
  const part = (callId: string, name: string, status: 'ok' | 'failed') =>
    fromPart({ kind: 'tool', callId, name, argsSummary: '', status, summary: '' });
  const ok = [part('a', 'list_agents', 'ok'), part('b', 'research', 'ok')];
  const mixed = [part('a', 'gm_command', 'ok'), part('b', 'research', 'failed'), part('c', 'list_agents', 'ok')];
  assert.equal(callRunSummary(ok).lead, 'Worked');
  assert.equal(callRunSummary(mixed).lead, 'Finished');
  assert.doesNotMatch(callRunSummary(mixed).lead, /Worked/);
  assert.equal(runSummary(mixed).lead, 'Worked');                       // kit unchanged
  const timed = (callId: string, status: 'ok' | 'failed', ts: string, resultTs: string) =>
    ({ ...fromPart({ kind: 'tool', callId, name: 'research', argsSummary: '', status, summary: '' }), ts, resultTs });
  const t = [timed('a', 'ok', '2026-11-27T10:00:00Z', '2026-11-27T10:00:04Z'),
             timed('b', 'failed', '2026-11-27T10:00:05Z', '2026-11-27T10:00:09Z')];
  assert.match(runSummary(t).lead, /^Worked for \d+s$/);                // with a duration: "Finished in 5s"
  assert.equal(callRunSummary(t).lead, runSummary(t).lead.replace(/^Worked for /, 'Finished in '));
  assert.equal(callRunSummary(t.slice(0, 1)).lead, runSummary(t.slice(0, 1)).lead);   // one ok call: unchanged
  // Unfinished work is never "Finished": still running reads Working, an interrupted call Stopped, failure or not.
  const running = [part('a', 'gm_command', 'failed'),
                   fromPart({ kind: 'tool', callId: 'r', name: 'research', argsSummary: '', status: 'running', summary: '' })];
  assert.equal(callRunSummary(running).lead, 'Working');
  assert.match(callRunSummary(running, true).lead, /^Stopped/);
});
