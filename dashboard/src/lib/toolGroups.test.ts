/**
 * RED-first for the Workbench tool-call grouping (harness-UX plan, capability C3).
 *
 * One turn's tool calls fold to a single line — "Worked for 1m 8s — edited 2 files, ran 3
 * commands" — that expands to the per-call rows. The summary rules are borrowed from T3 Code's
 * work log: file changes and commands outrank reads, and a failure is never counted as a
 * success. The same summary must come out of a seat transcript and an Arturo live stream, so
 * both are adapted into one neutral WorkItem first.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  summarizeTurn, formatDuration, groupToolRuns, fromToolBlock, fromToolPart, isInterrupted,
  type WorkItem,
} from './toolGroups.js';
import { buildRenderList, type ChatItem, type RenderNode, type ToolBlock } from './transcript.js';
import type { ToolPart } from './turnParts.js';

const ok = (tool: string, path?: string, ts?: string): WorkItem => ({ tool, path, status: 'ok', ts });
const failed = (tool: string, path?: string): WorkItem => ({ tool, path, status: 'failed' });

// ---- summarizeTurn -----------------------------------------------------------------------

test('the plan acceptance fixture: 2 reads, 1 edit, 1 failing command', () => {
  const s = summarizeTurn([ok('Read', 'a.ts'), ok('Read', 'b.ts'), ok('Edit', 'a.ts'), failed('Bash')]);
  assert.equal(s.headline, 'edited 1 file, ran 1 command (failed)');
  assert.equal(s.failed, 1);
});

test('file changes and commands outrank reads — reads are not named when they exist', () => {
  const s = summarizeTurn([ok('Read', 'x'), ok('Read', 'y'), ok('Read', 'z'), ok('Write', 'out.md')]);
  assert.equal(s.headline, 'edited 1 file');
});

test('edits count DISTINCT files, not edit calls', () => {
  const s = summarizeTurn([ok('Edit', 'a.ts'), ok('Edit', 'a.ts'), ok('Edit', 'b.ts')]);
  assert.equal(s.headline, 'edited 2 files');
});

test('a FAILED edit is never counted as an edited file', () => {
  const s = summarizeTurn([failed('Edit', 'a.ts'), ok('Edit', 'b.ts')]);
  assert.equal(s.headline, 'edited 1 file, 1 failed');
  assert.equal(s.failed, 1);
});

test('only failures: nothing is claimed as done', () => {
  const s = summarizeTurn([failed('Edit', 'a.ts'), failed('Bash')]);
  assert.equal(s.headline, '2 failed');
});

test('several failed commands report how many failed, not just "failed"', () => {
  const s = summarizeTurn([ok('Bash'), failed('Bash'), failed('Bash')]);
  assert.equal(s.headline, 'ran 3 commands (2 failed)');
});

test('reads and searches are named when nothing outranks them', () => {
  assert.equal(summarizeTurn([ok('Read', 'a'), ok('Read', 'b')]).headline, 'read 2 files');
  assert.equal(summarizeTurn([ok('Grep'), ok('Glob'), ok('Read', 'a')]).headline, 'read 1 file, searched 2 times');
});

test('an unknown tool still produces an honest headline', () => {
  assert.equal(summarizeTurn([ok('mcp__x__do_thing'), ok('mcp__x__do_thing')]).headline, 'used 2 tools');
});

test('calls still running are reported as in progress, never as done', () => {
  const s = summarizeTurn([ok('Edit', 'a.ts'), { tool: 'Bash', status: 'running' }]);
  assert.equal(s.running, 1);
  assert.equal(s.headline, 'edited 1 file, running 1 command');
});

test('codex and gemini tool names map to the same categories', () => {
  const s = summarizeTurn([ok('apply_patch', 'a.ts'), ok('write_file', 'b.ts'), ok('run_shell_command'), ok('shell')]);
  assert.equal(s.headline, 'edited 2 files, ran 2 commands');
});

test('duration spans first to last timestamp; absent when timestamps are missing', () => {
  const s = summarizeTurn([ok('Read', 'a', '2026-10-06T05:00:00Z'), ok('Edit', 'a', '2026-10-06T05:01:08Z')]);
  assert.equal(s.durationMs, 68_000);
  assert.equal(summarizeTurn([ok('Read', 'a'), ok('Edit', 'a')]).durationMs, undefined);
});

test('a single timestamp, or an unparseable one, gives no duration rather than a fake 0s', () => {
  assert.equal(summarizeTurn([ok('Read', 'a', '2026-10-06T05:00:00Z'), ok('Edit', 'a')]).durationMs, undefined);
  assert.equal(summarizeTurn([ok('Read', 'a', 'nope'), ok('Edit', 'a', 'also-nope')]).durationMs, undefined);
});

test('formatDuration', () => {
  assert.equal(formatDuration(45_000), '45s');
  assert.equal(formatDuration(68_000), '1m 8s');
  assert.equal(formatDuration(3_600_000), '1h 0m');
  assert.equal(formatDuration(400), '<1s');
});

// ---- adapters: one summary from two sources --------------------------------------------

test('a transcript ToolBlock adapts: path from file_path/path/notebook_path, status from result', () => {
  const b = (input: Record<string, unknown>, extra: Partial<ToolBlock> = {}): ToolBlock =>
    ({ kind: 'tool', tool: 'Edit', input, key: 'k', result: 'ok', ...extra });
  assert.deepEqual(fromToolBlock(b({ file_path: 'a.ts' }, { ts: 't' })), { tool: 'Edit', path: 'a.ts', status: 'ok', ts: 't' });
  assert.equal(fromToolBlock(b({ path: 'p.md' })).path, 'p.md');
  assert.equal(fromToolBlock(b({ notebook_path: 'n.ipynb' })).path, 'n.ipynb');
  assert.equal(fromToolBlock(b({}, { isError: true })).status, 'failed');
  assert.equal(fromToolBlock(b({}, { result: undefined })).status, 'running');
});

test('transcript and Arturo streams give the headline each can actually WARRANT', () => {
  const blocks: ToolBlock[] = [
    { kind: 'tool', tool: 'Read', input: { file_path: 'a' }, result: 'x', key: '1' },
    { kind: 'tool', tool: 'Bash', input: { command: 'ls' }, result: 'boom', isError: true, key: '2' },
    { kind: 'tool', tool: 'Write', input: { file_path: 'r.md' }, result: 'ok', key: '3' },
  ];
  const parts: ToolPart[] = [
    { kind: 'tool', callId: '1', name: 'Read', argsSummary: 'a', status: 'ok' },
    { kind: 'tool', callId: '2', name: 'Bash', argsSummary: 'ls', status: 'failed' },
    { kind: 'tool', callId: '3', name: 'Write', argsSummary: 'r.md', status: 'ok' },
  ];
  // NOT assert.equal(a, b): the two adapters legitimately DIFFER, because the transcript knows
  // paths and the Arturo stream does not. Each side is pinned to the exact string it must produce,
  // so a regression on either one cannot hide behind the other agreeing with it.
  assert.equal(summarizeTurn(blocks.map(fromToolBlock)).headline, 'edited 1 file, ran 1 command (failed)');
  assert.equal(summarizeTurn(parts.map(fromToolPart)).headline, 'made 1 edit, ran 1 command (failed)');
});

// ---- groupToolRuns: where the fold happens ----------------------------------------------

const tool = (k: string, name = 'Read'): RenderNode =>
  ({ kind: 'tool', tool: name, input: {}, result: 'r', key: k });
const say = (k: string): RenderNode => ({ kind: 'assistant', text: 'hi', key: k });
const think = (k: string): RenderNode => ({ kind: 'thinking', text: 'hmm', key: k });

test('two or more consecutive tool calls fold into ONE group, in order', () => {
  const out = groupToolRuns([say('a'), tool('t1'), tool('t2'), tool('t3'), say('b')]);
  assert.deepEqual(out.map((n) => n.kind), ['assistant', 'tool_group', 'assistant']);
  const g = out[1];
  assert.ok(g.kind === 'tool_group');
  assert.deepEqual(g.children.map((c) => c.key), ['t1', 't2', 't3']);
});

test('a lone tool call stays a plain tool card — no group for a single call', () => {
  const out = groupToolRuns([say('a'), tool('t1'), say('b')]);
  assert.deepEqual(out.map((n) => n.kind), ['assistant', 'tool', 'assistant']);
});

test('thinking BETWEEN tool calls joins the group instead of splitting it', () => {
  const out = groupToolRuns([tool('t1'), think('h'), tool('t2')]);
  assert.equal(out.length, 1);
  assert.ok(out[0].kind === 'tool_group');
  assert.deepEqual(out[0].children.map((c) => c.key), ['t1', 'h', 't2']);
});

test('thinking at the EDGE of a run stays outside the group', () => {
  const out = groupToolRuns([think('h0'), tool('t1'), tool('t2'), think('h1'), say('b')]);
  assert.deepEqual(out.map((n) => n.kind), ['thinking', 'tool_group', 'thinking', 'assistant']);
});

test('thinking with only one tool call does not manufacture a group', () => {
  const out = groupToolRuns([tool('t1'), think('h'), say('b')]);
  assert.deepEqual(out.map((n) => n.kind), ['tool', 'thinking', 'assistant']);
});

test('user and assistant messages always break a run', () => {
  const out = groupToolRuns([tool('t1'), tool('t2'), { kind: 'user', text: 'u', key: 'u' }, tool('t3'), tool('t4')]);
  assert.deepEqual(out.map((n) => n.kind), ['tool_group', 'user', 'tool_group']);
});

test('nothing is dropped or reordered: flattening the groups gives back the input', () => {
  const input = [say('a'), tool('t1'), think('h'), tool('t2'), say('b'), tool('t3'), think('z')];
  const flat = groupToolRuns(input).flatMap((n) => (n.kind === 'tool_group' ? n.children : [n]));
  assert.deepEqual(flat.map((n) => n.key), input.map((n) => n.key));
});

test('the group key is stable and derived from its first child (React reconciliation)', () => {
  const a = groupToolRuns([tool('t1'), tool('t2')]);
  const b = groupToolRuns([tool('t1'), tool('t2'), tool('t3')]);
  assert.equal(a[0].key, b[0].key);
});

// ---- duration runs to when the LAST RESULT came back, not when the last call started -----
// Found by screenshot: a turn whose final call was an 8s test run read "Worked for 57s", not
// 1m 8s, because only call-start times were known. A long final command (a 10-minute test
// suite) would have vanished from "Worked for" entirely.

test('duration ends at the last result, not the last call start', () => {
  const s = summarizeTurn([
    { tool: 'Read', status: 'ok', ts: '2026-10-06T05:00:00Z', endTs: '2026-10-06T05:00:01Z' },
    { tool: 'Bash', status: 'ok', ts: '2026-10-06T05:01:00Z', endTs: '2026-10-06T05:01:08Z' },
  ]);
  assert.equal(s.durationMs, 68_000);
});

test('a lone call with a start and an end gives that call\'s duration', () => {
  const s = summarizeTurn([{ tool: 'Bash', status: 'ok', ts: '2026-10-06T05:00:00Z', endTs: '2026-10-06T05:00:10Z' }]);
  assert.equal(s.durationMs, 10_000);
});

test('the transcript adapter carries the result time as endTs', () => {
  const b: ToolBlock = { kind: 'tool', tool: 'Bash', input: {}, result: 'ok', ts: 'a', resultTs: 'b', key: 'k' };
  assert.equal(fromToolBlock(b).endTs, 'b');
});

test('buildRenderList pairs the result TIME onto its call, not only its text', () => {
  const items = [
    { kind: 'tool_use', id: 'T1', tool: 'Bash', input: { command: 'npm test' }, ts: '2026-10-06T05:01:00Z', key: 'c1' },
    { kind: 'tool_result', tool_use_id: 'T1', text: 'PASS', is_error: false, ts: '2026-10-06T05:01:08Z', key: 'r1' },
  ] as unknown as ChatItem[];
  const [node] = buildRenderList(items);
  assert.ok(node.kind === 'tool');
  assert.equal(node.ts, '2026-10-06T05:01:00Z');
  assert.equal(node.resultTs, '2026-10-06T05:01:08Z');
});

// ---- clearing review of #187 (REJECT): the tests did not hold the PR's own rules ---------
// The reviewer broke the code ten new ways and nine survived all 27 tests. Each test below is
// named for the sabotage it kills (S1..S11). "Never count a failure as done" and "running reads
// as running" had only been tested for edits and commands.

test('S6 a FAILED search is not counted as a search done', () => {
  assert.equal(summarizeTurn([failed('Grep'), ok('Grep')]).headline, 'searched 1 time, 1 failed');
});

test('S8 a FAILED read is not counted as a file read', () => {
  assert.equal(summarizeTurn([failed('Read', 'a'), ok('Read', 'b')]).headline, 'read 1 file, 1 failed');
});

test('S7 a FAILED unknown tool is not counted as a tool used', () => {
  assert.equal(summarizeTurn([failed('mcp__x__go'), ok('mcp__x__go')]).headline, 'used 1 tool, 1 failed');
});

test('S9 a RUNNING edit is not counted as an edited file', () => {
  assert.equal(summarizeTurn([ok('Edit', 'a'), { tool: 'Edit', path: 'b', status: 'running' }]).headline,
    'edited 1 file, running 1');
});

test('S2 a running call outside the command clause is still reported', () => {
  assert.equal(summarizeTurn([ok('Edit', 'a'), { tool: 'Read', path: 'r', status: 'running' }]).headline,
    'edited 1 file, running 1');
});

test('S11 an Arturo call still in flight stays running through the adapter', () => {
  const p: ToolPart = { kind: 'tool', callId: 'c', name: 'Bash', argsSummary: 'ls', status: 'running' };
  assert.equal(fromToolPart(p).status, 'running');
  const parts: ToolPart[] = [{ kind: 'tool', callId: '1', name: 'Edit', argsSummary: 'a', status: 'ok' }, p];
  const blocks: ToolBlock[] = [
    { kind: 'tool', tool: 'Edit', input: { file_path: 'a' }, result: 'ok', key: '1' },
    { kind: 'tool', tool: 'Bash', input: { command: 'ls' }, key: '2' },
  ];
  assert.equal(summarizeTurn(parts.map(fromToolPart)).headline, 'made 1 edit, running 1 command');
  assert.equal(summarizeTurn(blocks.map(fromToolBlock)).headline, 'edited 1 file, running 1 command');
});

test('S3 a call that returned EMPTY output is finished, not running', () => {
  const b: ToolBlock = { kind: 'tool', tool: 'Bash', input: {}, result: '', key: 'k' };
  assert.equal(fromToolBlock(b).status, 'ok');
});

test('S5 a FAILED final call keeps its result time, so its runtime stays in "Worked for"', () => {
  const items = [
    { kind: 'tool_use', id: 'T1', tool: 'Bash', input: {}, ts: '2026-10-06T05:00:00Z', key: 'c1' },
    { kind: 'tool_result', tool_use_id: 'T1', text: 'FAIL', is_error: true, ts: '2026-10-06T05:10:00Z', key: 'r1' },
  ] as unknown as ChatItem[];
  const [node] = buildRenderList(items);
  assert.ok(node.kind === 'tool');
  assert.equal(node.resultTs, '2026-10-06T05:10:00Z');
  assert.equal(summarizeTurn([fromToolBlock(node)]).durationMs, 600_000);
});

test('S1 every key in a grouped list is unique, across several groups', () => {
  const out = groupToolRuns([tool('t1'), tool('t2'), say('a'), tool('t3'), tool('t4'), say('b'), tool('t5'), tool('t6')]);
  const keys = out.map((n) => n.key);
  assert.equal(new Set(keys).size, keys.length, `duplicate keys: ${keys.join(',')}`);
});

test('S4 Codex exec_command is a command', () => {
  assert.equal(summarizeTurn([ok('exec_command')]).headline, 'ran 1 command');
});

// ---- Antigravity (Gemini CLI) names: the API's own toolSummary table is the source ------

test('Antigravity edit, read and search names map, with TargetFile/AbsolutePath as paths', () => {
  const blk = (tool: string, input: Record<string, unknown>): ToolBlock => ({ kind: 'tool', tool, input, result: 'ok', key: tool });
  assert.equal(fromToolBlock(blk('write_to_file', { TargetFile: 'b.md' })).path, 'b.md');
  assert.equal(fromToolBlock(blk('view_file', { AbsolutePath: '/x/a.ts' })).path, '/x/a.ts');
  const edits = [blk('view_file', { AbsolutePath: 'a' }), blk('replace_file_content', { TargetFile: 'a' }), blk('write_to_file', { TargetFile: 'b' })];
  assert.equal(summarizeTurn(edits.map(fromToolBlock)).headline, 'edited 2 files');
  const mixed = [blk('replace_file_content', { TargetFile: 'a' }), blk('write_to_file', { TargetFile: 'b' }), blk('run_command', { CommandLine: 'ls' })];
  assert.equal(summarizeTurn(mixed.map(fromToolBlock)).headline, 'edited 2 files, ran 1 command');
  assert.equal(summarizeTurn([ok('grep_search'), ok('find_by_name')]).headline, 'searched 2 times');
  // view_file's CATEGORY, not just its path: the `edits` assertion above cannot see it,
  // because the two real edits produce 'edited 2 files' on their own.
  assert.equal(summarizeTurn([ok('view_file'), ok('view_file')]).headline, 'made 2 reads');
  // search_web appeared in no assertion at all.
  assert.equal(summarizeTurn([ok('search_web'), ok('search_web')]).headline, 'searched 2 times');
});

// ---- a call that will never finish must not read "Working" forever -----------------------

test('an interrupted turn reports its unfinished calls as interrupted, not running', () => {
  const s = summarizeTurn([ok('Edit', 'a'), { tool: 'Bash', status: 'running' }], { interrupted: true });
  assert.equal(s.headline, 'edited 1 file, 1 interrupted');
  assert.equal(s.running, 0);
  assert.equal(s.interrupted, 1);
});

test('interrupted only changes calls that were still running', () => {
  assert.equal(summarizeTurn([ok('Edit', 'a'), failed('Bash')], { interrupted: true }).headline,
    'edited 1 file, ran 1 command (failed)');
});

test('isInterrupted: only on EVIDENCE -- later content, or a known stopped state', () => {
  assert.equal(isInterrupted({ isLast: false, state: 'working' }), true);   // the turn moved on
  assert.equal(isInterrupted({ isLast: true, state: 'idle' }), true);       // agent back at prompt
  assert.equal(isInterrupted({ isLast: true, state: 'crashed' }), true);
  assert.equal(isInterrupted({ isLast: true, state: 'queued' }), true);     // turn ENDED, message never ran
  assert.equal(isInterrupted({ isLast: true, state: 'working' }), false);
  assert.equal(isInterrupted({ isLast: true, state: 'stalled' }), false);   // long turn, still working
  assert.equal(isInterrupted({ isLast: true, state: 'unknown' }), false);   // no evidence, no claim
});

test('a call waiting on a PERMISSION prompt is not interrupted (state "waiting")', () => {
  assert.equal(isInterrupted({ isLast: true, state: 'waiting' }), false);
});

// ---- the honest-count gate (DEC-1791269555817143 v6) ---------------------------------------
// The headline must never name a number of FILES it cannot know. Two edits to ONE file through
// a tool whose path is unknowable (Codex `apply_patch`) used to read "edited 2 files".

const patch = (): WorkItem => ({ tool: 'apply_patch', status: 'ok' });
const named = (tool: string, path: string): WorkItem => ({ tool, path, status: 'ok' });

test('THE DEFECT: unnamed edit calls are counted as CALLS, never invented as files', () => {
  // M1's explicit string assertion. The invariant below also catches this (2 claimed files
  // against 0 known paths), but the invariant's match is wording-dependent, so the exact text a
  // user reads is pinned here too.
  assert.equal(summarizeTurn([patch(), patch()]).headline, 'made 2 edits');
  assert.equal(summarizeTurn([named('Edit', 'a.ts'), named('Edit', 'a.ts')]).headline, 'edited 1 file');
  // M6: the singular boundary needs exactly ONE ok call in the category, because two calls
  // render "made 2 edits" and would make this branch unreachable.
  assert.equal(summarizeTurn([patch()]).headline, 'made 1 edit');
  assert.equal(summarizeTurn([{ tool: 'read_many_files', status: 'ok' }]).headline, 'made 1 read');
});

test('M2 a MIXED category reports CALLS for the whole category, never a file count', () => {
  // Reachable within ONE adapter: a Codex turn mixing Edit (path known) with apply_patch (not).
  const mixed = [named('Edit', 'a.ts'), named('Edit', 'a.ts'), patch(), patch()];
  const h = summarizeTurn(mixed).headline;
  assert.equal(h, 'made 4 edits');
  // A word boundary, not a substring: a headline carrying a path like src/file.ts must not trip.
  assert.ok(!/\bfiles?\b/.test(h), `mixed turn named a file unit: ${h}`);
});

test('M3 the read clause obeys the same rule (its own call site, pinned separately)', () => {
  // NO edit and NO command in this fixture: the read clause sits behind `clauses.length === 0`,
  // so a stray Bash would make a broken read clause pass.
  assert.equal(summarizeTurn([named('Read', 'a'), named('Read', 'a')]).headline, 'read 1 file');
  assert.equal(summarizeTurn([{ tool: 'read_many_files', status: 'ok' },
                              { tool: 'read_many_files', status: 'ok' }]).headline, 'made 2 reads');
});

test('M4 the clause-gate: an all-unnamed edit turn must NOT fall through to the read clause', () => {
  // The likeliest implementation bug: countWork returns files:0 and a retained `files > 0`
  // test deletes the edit clause, so the headline silently becomes a READING turn.
  const h = summarizeTurn([patch(), patch(), named('Read', 'z.ts')]).headline;
  assert.equal(h, 'made 2 edits');
  assert.ok(!/read/.test(h), `an editing turn reported reading: ${h}`);
});

test('M5 a failed or running unnamed edit is not work done', () => {
  assert.equal(summarizeTurn([{ tool: 'apply_patch', status: 'failed' }]).headline, '1 failed');
  assert.equal(summarizeTurn([patch(), { tool: 'apply_patch', status: 'failed' }]).headline,
    'made 1 edit, 1 failed');
  assert.equal(summarizeTurn([patch(), { tool: 'apply_patch', status: 'running' }]).headline,
    'made 1 edit, running 1');
});

test('M7 a named and an unnamed call in one category do not dedupe into each other', () => {
  // files:1, unnamed:1, calls:2 — the unnamed call must not be absorbed by the known path.
  assert.equal(summarizeTurn([named('Edit', 'a.ts'), patch()]).headline, 'made 2 edits');
});

test('INVARIANT: the headline never claims more files than it can know (4 parts)', () => {
  // NOTE: the ^...$ anchors mean every generated row must produce a SINGLE clause. Adding a
  // command or a failure row to the table below would make these go red against a CORRECT
  // implementation — extend the table and the anchors together, or not at all.
  const KNOWN_EDIT = [/^edited (\d+) files?$/, /^made (\d+) edits?$/];
  const KNOWN_READ = [/^read (\d+) files?$/, /^made (\d+) reads?$/];
  const rows: WorkItem[][] = [];
  const paths = [undefined, 'a.ts', 'b.ts'];
  for (const p1 of paths) for (const p2 of paths) for (const p3 of paths) {
    for (const tool of ['Edit', 'Read']) {
      rows.push([p1, p2, p3].map((p) => (p ? { tool, path: p, status: 'ok' as const }
                                           : { tool, status: 'ok' as const })));
    }
  }
  const shapesSeen = new Set<string>();
  for (const items of rows) {
    const h = summarizeTurn(items).headline;
    const isRead = items[0].tool === 'Read';
    const known = isRead ? KNOWN_READ : KNOWN_EDIT;
    const distinct = new Set(items.filter((i) => i.path).map((i) => i.path!)).size;
    const okCalls = items.filter((i) => i.status === 'ok').length;
    const unnamed = items.filter((i) => !i.path).length;

    // (iv) POSITIVE EVIDENCE: an unrecognised phrasing must FAIL, not pass silently. Without
    // this, renaming the clause would make every regex below MISS and the whole loop vacuous.
    const m = known.map((re) => re.exec(h)).find((x) => x) ?? null;
    assert.ok(m, `headline matched no enumerated shape: ${h}`);
    shapesSeen.add(m![0].replace(/\d+/, 'N').replace(/(file|edit|read)s?$/, '$1'));
    const n = Number(m![1]);

    if (/^(edited|read) /.test(h)) {
      // (i) upper bound AND (ii) exact equality, which only holds when every path is known.
      assert.ok(n <= distinct, `claimed ${n} files against ${distinct} known paths: ${h}`);
      assert.equal(unnamed, 0, `named a FILE unit with ${unnamed} unknown paths: ${h}`);
      assert.equal(n, distinct, `file count ${n} != distinct known paths ${distinct}: ${h}`);
    } else {
      // (iii) the made-N number is the TOTAL ok calls in the category, NOT the unnamed subset.
      // Scoped to the total BECAUSE of the collapse; do not "fix" this back to `unnamed`.
      assert.equal(n, okCalls, `made ${n} against ${okCalls} ok calls: ${h}`);
    }
  }
  // (iv), second half: every shape must have been exercised, so none of the above is vacuous.
  assert.deepEqual([...shapesSeen].sort(), ['edited N file', 'made N edit', 'made N read', 'read N file']);
});

test('the Arturo adapter is a SUBSET of the transcript adapter, with path free to arrive', () => {
  // Deliberately NOT "path must be absent": giving the wire a real path later must stay GREEN.
  // Dropping `status` or renaming `tool` must go red.
  const part: ToolPart = { kind: 'tool', callId: 'c', name: 'Edit', argsSummary: 'a.ts', status: 'ok' };
  const block: ToolBlock = { kind: 'tool', tool: 'Edit', input: { file_path: 'a.ts' }, result: 'ok', key: 'c' };
  const fromPart = fromToolPart(part) as Record<string, unknown>;
  const fromBlock = fromToolBlock(block) as Record<string, unknown>;
  for (const [k, v] of Object.entries(fromPart)) {
    assert.ok(k in fromBlock, `adapter emits a key the transcript adapter does not: ${k}`);
    assert.equal(v, fromBlock[k], `adapters disagree on ${k}`);
  }
  assert.ok('tool' in fromPart && 'status' in fromPart, 'the subset must still carry tool and status');
});
