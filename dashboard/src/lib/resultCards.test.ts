import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  taskResult, subagentResult, jobResult, handbackResult, isHandback, markResults, stackRuns, stackHeadline, newestFirst,
  cleanSubagentReport, linksAndFiles, type FinishedResult,
} from './threadKit/results.ts';
import type { RenderNode, ToolBlock } from './transcript.ts';
import { groupToolRuns } from './toolGroups.ts';

// Fixtures cut from real records on the development box, names made neutral (Sample, Example, pm-demo, dev-demo).

// Arturo's research job (threads.db turn, meta kind job_result), its text shortened.
const RESEARCH_META = { kind: 'job_result', job_id: 'job_d61eaf1c1e', job_kind: 'research', title: 'Grading the Sample website', status: 'done',
  steps: [{ call_id: 'c1', name: 'WebFetch', args_summary: 'https://example.com/', ok: true, summary: 'fetched' },
          { call_id: 'c2', name: 'WebFetch', args_summary: 'https://example.com/about', ok: true, summary: 'fetched' }] };
const RESEARCH_TEXT = "Grading the Sample website: done.\n\nI've visited the homepage and seven inner pages and looked at two competitor sites. Here's the full report.\n\n# Sample Website Grade\n\n" + 'Detail line about the site. '.repeat(30);
// His deep-think job: the posted text has no "<title>:" line.
const THINK_META = { kind: 'job_result', job_id: 'job_365d6f1c49', job_kind: 'deep_think', title: 'Idle agents and what they should do', status: 'done', steps: [] };
const THINK_TEXT = 'Example, all three seats are idle, and nothing is assigning them work. The roadmap is empty: no projects, tasks or blockers.';
// A one-paragraph job with no steps.
const SMOKE_META = { kind: 'job_result', job_id: 'job_8d4ae4fb1a', job_kind: 'research', title: 'Looking into example.com', status: 'done' };
const SMOKE_TEXT = "Looking into example.com: done.\n\nexample.com is a placeholder domain set aside for documentation. It isn't a real business or service.";

// msg_store rows: a phase_report (a hand-back) and a status message (stays a message line).
const PHASE_REPORT = { id: 'msg_ada9551b_74114293', type: 'phase_report', from_agent: 'pm-demo', subject: 'DELIVERABLE SHIPPED + the render DISAGREED with me: mobile shows 1 button, not 3',
  created_at: '2026-10-24T20:35:14+00:00',
  body: 'Deliverable: /home/demo/.orchestra/projects/sample/deliverables/2026-10-24-sample-audit-verification.md\n\n=== FIRST: THE CORRECTION I PROMISED YOU ===\nI said I would send the render result if it disagreed with my markup reading. ' + 'More of the report. '.repeat(40) };
const STATUS_MSG = { id: 'msg_9d64c05e_73132055', type: 'status', from_agent: 'dev-demo', subject: 'TASK A complete: a-e,g,h,i verified', created_at: '2026-10-24T20:18:52Z', body: 'TASK A done.' };

// The harness's notice for a background command (Claude Code's <task-notification> envelope).
const notice = (status: string, summary: string, id = 'b1') =>
  `<task-notification>\n<task-id>${id}</task-id>\n<tool-use-id>toolu_${id}</tool-use-id>\n<output-file>/tmp/tasks/${id}.output</output-file>\n<status>${status}</status>\n<summary>${summary}</summary>\n</task-notification>\nRead the output file to retrieve the result: /tmp/tasks/${id}.output`;

test('an Arturo research job: title, outcome, its "title: done." line left out, steps kept, the long report behind "more"', () => {
  const r = jobResult(RESEARCH_META, RESEARCH_TEXT, { key: 'k', ts: 1792854380.97, by: 'Arturo', steps: [] })!;
  assert.equal(r.source, 'job');
  assert.equal(r.title, 'Grading the Sample website');
  assert.equal(r.outcome, 'done');
  assert.equal(r.by, 'Arturo');
  assert.ok(!r.summary.startsWith('Grading the Sample website:'), 'the header already says it is done');
  assert.ok(r.summary.startsWith("I've visited the homepage"));
  assert.ok(r.detail && r.detail.length > r.summary.length, 'the whole report opens with "more"');
  assert.equal(r.at, new Date(1792854380.97 * 1000).toISOString());
});

test('a deep-think job whose text has no title line, and a short job with no steps', () => {
  const t = jobResult(THINK_META, THINK_TEXT, { key: 'k2' })!;
  assert.equal(t.summary, THINK_TEXT);
  assert.equal(t.detail, undefined, 'short: nothing more to open');
  const s = jobResult(SMOKE_META, SMOKE_TEXT, { key: 'k3' })!;
  assert.match(s.summary, /^example\.com is a placeholder/);
  assert.equal(s.steps, undefined);
  assert.equal(jobResult({ kind: 'card' }, 'x', { key: 'k4' }), null, 'any other turn is not a result');
});

test('a job that did not finish is a failure with its reason shown', () => {
  const r = jobResult({ ...SMOKE_META, status: 'lost' }, "Looking into example.com: didn't finish (the service restarted).", { key: 'k' })!;
  assert.equal(r.outcome, 'failed');
  assert.match(r.error!, /didn't finish/);
  assert.equal(jobResult({ ...SMOKE_META, status: 'cancelled' }, 'x', { key: 'k' })!.outcome, 'cancelled');
});

test('a phase_report is a hand-back card; a status message is not', () => {
  assert.ok(isHandback('phase_report') && isHandback('handoff'));
  assert.ok(!isHandback('status') && !isHandback('reply') && !isHandback(null));
  const r = handbackResult(PHASE_REPORT, 'Sample PM')!;
  assert.equal(r.source, 'handback');
  assert.equal(r.outcome, 'reported');
  assert.equal(r.by, 'Sample PM');
  assert.match(r.title, /^DELIVERABLE SHIPPED/);
  assert.deepEqual(r.files, ['/home/demo/.orchestra/projects/sample/deliverables/2026-10-24-sample-audit-verification.md']);
  assert.ok(r.detail, 'a long report opens with "more"');
  assert.equal(handbackResult(STATUS_MSG), null);
});

test('a background task that finished, failed or was stopped', () => {
  const ok = taskResult(notice('completed', 'Background command "Sample check passes" completed (exit code 0)'), { key: 'n1', ts: '2026-10-30T00:04:00Z', by: 'dev-demo' })!;
  assert.equal(ok.source, 'task');
  assert.equal(ok.title, 'Sample check passes');
  assert.equal(ok.outcome, 'done');
  assert.equal(ok.error, undefined);
  const bad = taskResult(notice('failed', 'Background command "Sample check fails" failed with exit code 3', 'b2'), { key: 'n2' })!;
  assert.equal(bad.outcome, 'failed');
  assert.equal(bad.error, 'Exit code 3', 'plain words: the exit code only as the reason for a failure');
  assert.equal(ok.summary, '', "no harness line repeated: the title and badge say it");
  assert.equal(ok.launchId, 'toolu_b1');
  assert.equal(taskResult(notice('killed', 'Background command "Sample" was stopped', 'b3'), { key: 'n3' })!.outcome, 'cancelled');
  assert.equal(taskResult('<task-notification>\n<task-id>x</task-id>\n</task-notification>', { key: 'n4' }), null, 'nothing to show: drawn as before');
  assert.equal(taskResult('hello', { key: 'n5' }), null);
});

const subagent = (extra: Partial<ToolBlock> = {}): ToolBlock => ({
  kind: 'tool', tool: 'Agent', key: 's1', ts: '2026-10-30T00:03:20Z', resultTs: '2026-10-30T00:03:50Z',
  input: { description: 'Count lines in the README', subagent_type: 'general-purpose', prompt: 'Read the README.' },
  result: 'The README has 120 lines.\nFirst heading: # Sample\nagentId: a123 (for resuming)\n<usage>total_tokens: 900</usage>', ...extra,
});

test("a subagent's report: its description, its type, how long it took, the harness trailer stripped", () => {
  const r = subagentResult(subagent())!;
  assert.equal(r.title, 'Count lines in the README');
  assert.equal(r.by, 'general-purpose', 'the card already says Subagent');
  assert.equal(r.durationMs, 30_000);
  assert.equal(r.outcome, 'done');
  assert.equal(r.summary, 'The README has 120 lines.\nFirst heading: # Sample');
  assert.equal(subagentResult(subagent({ result: undefined })), null, 'still running: stays a tool row');
  assert.equal(subagentResult(subagent({ tool: 'Bash' })), null);
  const failed = subagentResult(subagent({ isError: true, result: 'Agent crashed: out of turns' }))!;
  assert.equal(failed.outcome, 'failed');
  assert.equal(failed.error, 'Agent crashed: out of turns');
  assert.equal(cleanSubagentReport('a\nagentId: x\n<usage>t</usage>'), 'a');
});

const say = (k: string): RenderNode => ({ kind: 'assistant', text: 'hi', key: k });
const person = (k: string): RenderNode => ({ kind: 'user', text: 'run the checks', key: k });
const mail = (k: string): RenderNode => ({ kind: 'user', text: '[MSG from pm-demo | normal] do X', key: k, machine: 'relay' });
const bash = (id: string): RenderNode => ({ kind: 'tool', tool: 'Bash', input: { command: 'sleep 1', run_in_background: true }, result: 'running', key: `k-${id}`, id: `toolu_${id}` });
const done = (id: string, s = 'completed', mid = true): RenderNode => ({ kind: 'user', text: notice(s, `Background command "${id}" ${s}`, id), key: `n-${id}`, machine: 'task', ...(mid ? { midTurn: true } : {}) });
const shape = (out: RenderNode[]) => out.map((x) => (x.kind === 'result' ? `result:${x.view}:${x.results.map((r) => r.title).join('+')}` : x.kind));

test('a turn the operator started: the reply covering a result is the message, the card a receipt under it', () => {
  const out = markResults([person('p'), bash('b1'), done('b1'), say('a')], 'dev-demo');
  assert.deepEqual(shape(out), ['user', 'tool', 'assistant', 'result:receipt:b1']);
  assert.equal((out[3] as Extract<RenderNode, { kind: 'result' }>).results[0].by, 'dev-demo', "a background job is the agent's own work");
});

test('two results one reply covers stack into one receipt under it', () => {
  const out = markResults([person('p'), bash('b1'), bash('b2'), done('b1'), done('b2'), say('a')]);
  assert.deepEqual(shape(out), ['user', 'tool', 'tool', 'assistant', 'result:receipt:b1+b2']);
});

test('no reply yet: the card shows open where it is', () => {
  assert.deepEqual(shape(markResults([person('p'), bash('b1'), done('b1')])), ['user', 'tool', 'result:open:b1']);
  // the operator spoke again before any reply: not covered either
  assert.deepEqual(shape(markResults([person('p'), bash('b1'), done('b1'), person('p2'), say('a')])),
    ['user', 'tool', 'result:open:b1', 'user', 'assistant']);
});

test('a routine job the agent started for itself that passed folds into the strip; it stays in place', () => {
  const out = markResults([mail('m'), bash('b1'), done('b1'), bash('b2'), say('a')]);
  assert.deepEqual(shape(out), ['user', 'tool', 'result:folded:b1', 'tool', 'assistant']);
  const grouped = groupToolRuns(out);
  assert.deepEqual(grouped.map((x) => x.kind), ['user', 'tool_group', 'assistant']);
  assert.deepEqual((grouped[1] as { children: RenderNode[] }).children.map((c) => c.kind), ['tool', 'result', 'tool'], 'inside the "Worked for…" strip');
  // the notice of a job the agent started, reported later at an idle prompt, is routine too
  assert.deepEqual(shape(markResults([mail('m'), bash('b1'), say('a'), done('b1', 'completed', false), say('a2')])),
    ['user', 'tool', 'assistant', 'result:folded:b1', 'assistant']);
});

test('a failure is never folded or collapsed: it shows open where it happened, whoever started it', () => {
  assert.deepEqual(shape(markResults([mail('m'), bash('b1'), done('b1', 'failed'), say('a')])), ['user', 'tool', 'result:open:b1', 'assistant']);
  assert.deepEqual(shape(markResults([person('p'), bash('b1'), done('b1', 'failed'), say('a')])), ['user', 'tool', 'result:open:b1', 'assistant']);
});

test('a job whose start is not in view is not assumed routine', () => {
  assert.deepEqual(shape(markResults([say('x'), done('gone', 'completed', false), say('a')])), ['assistant', 'assistant', 'result:receipt:gone']);
});

test('a synchronous subagent: a receipt in a turn the operator started, folded in a turn the agent started', () => {
  assert.deepEqual(shape(markResults([person('p'), subagent(), say('a')])), ['user', 'assistant', 'result:receipt:Count lines in the README']);
  assert.deepEqual(shape(markResults([mail('m'), subagent(), say('a')])), ['user', 'result:folded:Count lines in the README', 'assistant']);
  const running: RenderNode = { ...subagent(), result: undefined } as ToolBlock;
  assert.deepEqual(markResults([running]).map((x) => x.kind), ['tool'], 'a running subagent is still a tool row');
});

test('the stack headline counts each outcome', () => {
  const r = (o: 'done' | 'failed'): FinishedResult => ({ key: o + Math.random(), source: 'task', title: 't', outcome: o, summary: '', links: [], files: [] });
  assert.equal(stackHeadline([r('done'), r('done'), r('failed')]), '3 results · 2 done · 1 failed');
});

test('stack runs over feed items, and newest first inside a stack', () => {
  const r = (key: string, at?: string): FinishedResult => ({ key, source: 'job', title: key, outcome: 'done', summary: '', links: [], files: [], ...(at ? { at } : {}) });
  const runs = stackRuns([{ r: r('a') }, { r: r('b') }, {}, { r: r('c') }], (x: { r?: FinishedResult }) => x.r);
  assert.deepEqual(runs.map((x) => (x === undefined ? 'u' : x === null ? 'n' : x.map((y) => y.key).join(''))), ['ab', 'n', 'u', 'c']);
  assert.deepEqual(newestFirst([r('old', '2026-10-30T00:00:00Z'), r('new', '2026-10-30T01:00:00Z')]).map((x) => x.key), ['new', 'old']);
  assert.deepEqual(newestFirst([r('first'), r('second')]).map((x) => x.key), ['second', 'first'], 'no times: later in the thread first');
});

test('links and files a report names, each once', () => {
  const { links, files } = linksAndFiles('See https://example.com/sample-report. Read /home/demo/orchestraos/README.md and ~/notes/plan.md, again /home/demo/orchestraos/README.md');
  assert.deepEqual(links, ['https://example.com/sample-report']);
  assert.deepEqual(files, ['/home/demo/orchestraos/README.md', '~/notes/plan.md']);
});

// The notice a background command sent at an idle prompt (qa-sample throwaway, 2026-10-30), paths shortened.
const REAL_IDLE_NOTICE = '<task-notification>\n<task-id>bsz5wm0ru</task-id>\n<tool-use-id>toolu_sample_idle_notice</tool-use-id>\n<output-file>/tmp/demo/tasks/bsz5wm0ru.output</output-file>\n<status>completed</status>\n<summary>Background command "Sample check after the turn" completed (exit code 0)</summary>\n</task-notification>';

test('the real idle notice of a background command', () => {
  const r = taskResult(REAL_IDLE_NOTICE, { key: 'k', ts: '2026-10-30T00:04:40Z', by: 'dev-demo' })!;
  assert.equal(r.title, 'Sample check after the turn');
  assert.equal(r.source, 'task');
  assert.equal(r.outcome, 'done');
  assert.equal(r.summary, '');
  assert.equal(r.launchId, 'toolu_sample_idle_notice');
});

test('a background subagent: its launch note stays a tool row, its later notice is the result', () => {
  assert.equal(subagentResult(subagent({ result: 'Async agent launched successfully.\nagentId: a1 (internal ID)' })), null);
  const r = taskResult('<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n<summary>Agent "Count lines in the README" completed</summary>\n<result>The README has 120 lines.\nFirst heading: # Sample\nagentId: a1</result>\n</task-notification>', { key: 'k', by: 'dev-demo' })!;
  assert.equal(r.source, 'subagent');
  assert.equal(r.title, 'Count lines in the README');
  assert.equal(r.summary, 'The README has 120 lines.\nFirst heading: # Sample');
  assert.equal(r.by, 'dev-demo', 'the agent that ran it');
});

// The notice a background subagent sent mid-turn (qa-sample throwaway, 2026-10-30, read through the transcript
// API), paths shortened.
const REAL_SUBAGENT_NOTICE = '<task-notification>\n<task-id>a72224c78fd876422</task-id>\n<tool-use-id>toolu_sample_subagent</tool-use-id>\n<output-file>/tmp/demo/tasks/a72224c78fd876422.output</output-file>\n<status>completed</status>\n<summary>Agent "Count lines in the README" finished</summary>\n<note>A task-notification fires each time this agent stops with no live background children of its own. The user can send it another message and resume it, so the same task-id may notify more than once.</note>\n<result>142 lines\nFirst heading: `# OrchestraOS`</result>\n<usage><subagent_tokens>40427</subagent_tokens><tool_uses>1</tool_uses><duration_ms>3874</duration_ms></usage>\n</task-notification>';
const REAL_FAILED_NOTICE = '<task-notification>\n<task-id>b1xm9rml7</task-id>\n<tool-use-id>toolu_sample_failed</tool-use-id>\n<output-file>/tmp/demo/tasks/b1xm9rml7.output</output-file>\n<status>failed</status>\n<summary>Background command "Sample check fails" failed with exit code 3</summary>\n</task-notification>';

test('the real mid-turn notices: a subagent report with its run time, and a failed command', () => {
  const s = taskResult(REAL_SUBAGENT_NOTICE, { key: 'k', ts: '2026-10-30T00:03:19.000Z', by: 'dev-demo' })!;
  assert.equal(s.source, 'subagent');
  assert.equal(s.title, 'Count lines in the README');
  assert.equal(s.summary, '142 lines\nFirst heading: `# OrchestraOS`');
  assert.equal(s.durationMs, 3874);
  assert.equal(s.outcome, 'done');
  const f = taskResult(REAL_FAILED_NOTICE, { key: 'k2' })!;
  assert.equal(f.outcome, 'failed');
  assert.equal(f.title, 'Sample check fails');
  assert.equal(f.error, 'Exit code 3');
});
