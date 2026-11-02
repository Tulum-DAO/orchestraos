// An agent's background work: live on the tool card that launched it (matched by tool_use_id), and a "⟳ N" pill at
// the end of the working row's line while the agent works. The separate job tray is gone (Arturo's stays).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fetchAgentJobs, jobsByToolUse, runningJobs, type AgentJob } from './agentJobs.ts';
import { fromBlock } from './toolCalls.ts';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('jobs carry the launching call (tool_use_id) and its thread row (launch_uuid); missing means no live status', async () => {
  const got = await fetchAgentJobs('dev-demo', (async () => new Response(JSON.stringify({ ok: true, jobs: [
    { id: 'j1', kind: 'command', title: 'npm test', status: 'running', started_at: '2026-01-01T10:00:00Z', tool_use_id: 'toolu_1', launch_uuid: 'u-1' },
    { id: 'j2', kind: 'subagent', title: 'Sample review', status: 'failed', started_at: '2026-01-01T09:00:00Z', ended_at: '2026-01-01T09:01:00Z', reason: 'was stopped', tool_use_id: 'toolu_2' },
    { id: 'j3', kind: 'wait', title: 'Waiting', status: 'running', started_at: '2026-01-01T09:30:00Z', tool_use_id: '' },
  ] }))) as typeof fetch) as AgentJob[];
  assert.deepEqual(got.map((j) => [j.id, j.tool_use_id ?? null, j.launch_uuid ?? null]), [['j1', 'toolu_1', 'u-1'], ['j2', 'toolu_2', null], ['j3', null, null]]);
  const m = jobsByToolUse(got);
  assert.deepEqual([...m.keys()], ['toolu_1', 'toolu_2']);
  assert.equal(m.get('toolu_2')!.reason, 'was stopped');
  assert.deepEqual(runningJobs(got).map((j) => j.id), ['j3', 'j1'], 'running only, oldest first');
});

test("a tool card knows its call's tool_use id, and shows the launched job's live status in an agent's thread", () => {
  const v = fromBlock({ kind: 'tool', tool: 'Bash', input: { command: 'npm test', run_in_background: true }, result: 'Command running in background with ID: b1', id: 'toolu_1', key: 'k1' });
  assert.equal(v.toolUseId, 'toolu_1');
  assert.equal(fromBlock({ kind: 'tool', tool: 'Read', input: {}, key: 'k2' }).toolUseId, undefined);
  const card = src('components/kit/ToolCard.tsx');
  assert.match(card, /\{job \? <JobLive job=\{job\} \/> : <StatusMark status=\{call\.status\} \/>\}/);
  assert.match(card, /call\.toolUseId \? jobs\?\.get\(call\.toolUseId\) : undefined/);
  assert.match(card, /failedLabel\(job\)/);
  assert.match(src('components/chat/TranscriptChatView.tsx'), /<LiveJobsContext\.Provider value=\{liveJobs\}>/);
});

test('no tray or pill above the composer; the jobs live in the slideout History, under Running, and lead to their card', () => {
  for (const f of ['components/agent/AgentJobTray.tsx', 'components/agent/JobsPill.tsx']) {
    assert.equal(existsSync(new URL(`../${f}`, import.meta.url)), false, f);
  }
  assert.ok(existsSync(new URL('../components/arturo/JobTray.tsx', import.meta.url)), "Arturo's tray stays");
  const page = src('pages/AgentPage.tsx');
  assert.doesNotMatch(page, /AgentJobTray|JobsPill/);
  assert.match(page, /usePollAgentJobs\(agentId\)/);
  assert.match(page, /subagents: Math\.max\([^\n]*runningBg\.length\) \|\| undefined/);
  const history = src('components/arturo/DrawerHistory.tsx');
  assert.match(history, /<AgentRunningJobs agentId=\{agentId\} onClose=\{onClose\} fallback=\{<AgentRunning subagents=\{subagents\} name=\{name\} \/>\} \/>/);
  const block = src('components/agent/AgentRunningJobs.tsx');
  assert.match(block, /if \(!shown\.length\) return <>\{fallback\}<\/>;/);
  assert.match(block, /onClose\(\);[\s\S]*focusWork\(uuid\)/);
  assert.match(block, /failedLabel\(j\)/);
  assert.match(block, /dismiss\(j\.id\)/);
});

test('the working row: no box, the avatar then the phrase; shimmer while it works or types (the only times it shows)', () => {
  const row = src('components/agent/AgentWorkingRow.tsx');
  assert.match(row, /const shown = isTyping \|\| rowShown\(row\);/);
  assert.match(row, /className="agent-working-phrase is-shimmer"/);
  const css = src('components/agent/agentWorking.css');
  assert.match(css, /@keyframes agent-shimmer-win\{ from\{ transform:translateX\(-100%\); \} to\{ transform:translateX\(250%\); \} \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)\{\s*\.agent-shimmer-win\{ display:none; \}/);
  const box = css.match(/\.agent-working\{ position:relative; display:flex;[^}]*\}/)![0];
  assert.doesNotMatch(box, /background|border|box-shadow|radius/, 'no enclosed chip');
  // its ground is the page colour across the whole column (not a box around it), and goes with the row
  assert.match(css, /\.agent-working::before\{[^}]*left:-100vw; right:-100vw; background:var\(--color-background\)/);
});

test('the row floats on top of the dock (the dock never resizes for it); no "writing…" line; the queued hint only with a draft', () => {
  const page = src('pages/AgentPage.tsx');
  assert.match(page, /<div ref=\{rowSlotRef\} className="absolute inset-x-0 bottom-full" data-testid="agent-working-slot">/);
  assert.match(page, /dock\.getBoundingClientRect\(\)\.height \+ \(rowSlotRef\.current\?\.getBoundingClientRect\(\)\.height \?\? 0\)/);
  assert.match(page, /ro\.observe\(rowSlotRef\.current\)/);
  const thread = src('components/kit/ThreadView.tsx');
  assert.doesNotMatch(thread, /writing…|data-paragraph-pending/);
  assert.match(thread, /\{live && \(skin !== 'bubbles' \|\| hasFinished\(live\.text, live\.parts\)\) && \(/);
  assert.match(src('components/agent/Composer.tsx'), /gate\.queued && draft\.trim\(\) !== '' && \(/);
});

test('the dock is solid in the page colour behind the row and the hint, with only a short fade on its top edge', () => {
  const page = src('pages/AgentPage.tsx');
  assert.match(page, /data-dock-solid className="absolute inset-0 bg-background"/);
  assert.match(page, /data-dock-fade className="absolute inset-x-0 -top-3 h-3 bg-gradient-to-b from-transparent to-background"/);
  assert.doesNotMatch(page, /via-background\/90/);
});
