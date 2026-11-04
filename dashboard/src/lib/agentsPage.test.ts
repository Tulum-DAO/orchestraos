/**
 * Settings › Agents (settings spec §4.5, the operator's brief 2026-11-03): the page's helpers, and the page read as source
 * (it imports CSS, so node does not render it): the OpenBot layout (intro, then Defaults, Safety limits, Background
 * processes), the "N of M use their own" line under each compaction default (none under Default model), the process
 * rows (command shortened, full on hover and on expand; agent or "No agent"; started time in the install's zone;
 * ports; Stop for an admin only, with one confirm), and the link from an agent to its own panel.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  agentPanelPath, overrideCount, overrideValueText, parseOverrides, parseProcesses, portsText, shortCommand, startedText,
  stopConsequence, stopProcess, wantsAgentPanel, processLabel, OPEN_AGENT_PANEL,
} from './agentsPage.ts';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');
const page = () => read('components/settings/sections/AgentsSection.tsx');
const order = (src: string, marks: string[]) => marks.map((m) => { const i = src.indexOf(m); assert.ok(i >= 0, `missing ${m}`); return i; });

test('the page: intro, then Defaults, Safety limits, Background processes; cards, no scope-tag groups', () => {
  const p = page();
  assert.match(p, /<PageIntro>Defaults every agent starts with, and what they leave running\.<\/PageIntro>/);
  const at = order(p, ['<Card title="Defaults" description="Agents follow these defaults unless you change them for an agent.">',
    '<Card title="Safety limits">', '<Card title="Background processes" description="Programs agents started and left running, such as dev servers. They keep running after the chat ends.">']);
  assert.deepEqual([...at].sort((a, b) => a - b), at);
  assert.doesNotMatch(p, /<Group /);
  const defaults = p.slice(p.indexOf('<Card title="Defaults"'), p.indexOf('<Card title="Safety limits">'));
  order(defaults, ['<DefaultModelRow />', 'k="agents.autoCompact"', 'k="agents.compactAt"', 'k="agents.showCompaction"']);
  // the existing stores, picker and stepper are unchanged
  assert.match(p, /useServerSetting\('agents\.defaultModel'\)[\s\S]*variant="inline"/);
  assert.match(defaults, /min=\{COMPACT_MIN\} max=\{COMPACT_MAX\} stepper/);
  assert.match(p, /<SafetyLimitsRow \/>/);
});

test('each default says its scope in words; the three compaction rows carry the count, Default model does not', () => {
  const p = page();
  assert.match(p, /const INSTALL_WIDE = 'For the whole install\.';/);
  for (const k of ['agents.autoCompact', 'agents.compactAt', 'agents.showCompaction']) {
    assert.match(p, new RegExp(`k="${k.replace('.', '\\.')}"[\\s\\S]*?description=\\{\`[^\`]+\\$\\{INSTALL_WIDE\\}\`\\} below=\\{count\\('${k.replace('.', '\\.')}'\\)\\}`));
  }
  const model = p.slice(p.indexOf('function DefaultModelRow'), p.indexOf('function SafetyLimitsRow'));
  assert.match(model, /An agent keeps the model it was made with\. \$\{INSTALL_WIDE\}/);
  assert.doesNotMatch(model, /OverrideCount|below=/);
  // ServerRows passes `below` through to the row
  const rows = read('components/settings/ServerRows.tsx');
  assert.equal((rows.match(/below=\{below\}/g) ?? []).length, 2);
});

test('overrides: parsed defensively; "N of M" with the right words; values in the row\'s words', () => {
  const r = parseOverrides({ ok: true, total: 3, agents: ['a', 'b', 'c'], rows: { 'agents.autoCompact': [{ id: 'a', value: true }, { id: 7 }], 'agents.compactAt': 'x' }, notCounted: { 'agents.defaultModel': 'why' } });
  assert.ok(r.ok);
  assert.deepEqual(r.ok && r.value.rows, { 'agents.autoCompact': [{ id: 'a', value: true }], 'agents.compactAt': [] });
  assert.equal(parseOverrides({ ok: false, error: 'nope' }).ok, false);
  assert.equal(parseOverrides(null).ok, false);
  assert.deepEqual(overrideCount([{ id: 'a', value: true }], 3), { n: 1, of: '1 of 3', rest: ' use their own' });
  assert.deepEqual(overrideCount([], 3), { n: 0, of: '0 of 3', rest: ' use their own' });
  assert.deepEqual(overrideCount([{ id: 'a', value: true }], 1), { n: 1, of: '1 of 1', rest: ' uses its own' });
  assert.equal(overrideCount(undefined, 3), null);
  assert.equal(overrideCount([], 0), null);
  assert.equal(overrideValueText('agents.autoCompact', true), 'On');
  assert.equal(overrideValueText('agents.showCompaction', false), 'Off');
  assert.equal(overrideValueText('agents.compactAt', 70), '70%');
});

test('the count opens a list of those agents, each a link to its own panel', () => {
  const p = page();
  const oc = p.slice(p.indexOf('function OverrideCount'), p.indexOf('function DefaultModelRow'));
  assert.match(oc, /aria-expanded=\{open\} aria-controls=\{listId\}/);
  assert.match(oc, /<strong>\{c\.of\}<\/strong>\{c\.rest\}/);
  assert.match(oc, /<AgentLink id=\{o\.id\} name=\{nameOf\(o\.id\)\} \/>/);
  assert.match(p, /navigate\(agentPanelPath\(id\), \{ state: \{ \[OPEN_AGENT_PANEL\]: true \} \}\)/);
  assert.equal(agentPanelPath('qa b'), '/agent/qa%20b');
  assert.ok(wantsAgentPanel({ [OPEN_AGENT_PANEL]: true }) && !wantsAgentPanel(null) && !wantsAgentPanel({ openAgentPanel: 'yes' }));
  // the agent page opens its panel once per such navigation
  const ap = read('pages/AgentPage.tsx');
  assert.match(ap, /if \(wantsAgentPanel\(location\.state\) && panelFor !== location\.key\) \{ setPanelFor\(location\.key\); setPanelOpen\(true\); \}/);
  // …then the state is cleared (replace), so Back or a reload doesn't open it again
  assert.match(ap, /navigate\(\{ pathname: location\.pathname, search: location\.search, hash: location\.hash \}, \{ replace: true, state: null \}\)/);
});

test('processes: parsed defensively; Stop only on an explicit canStop', () => {
  const p = { pid: 5, startTicks: 9, startedAt: '2026-11-03T19:13:27Z', program: 'node vite', ports: [5173], agent: 'dev-a', via: 'runtime' };
  const r = parseProcesses({ ok: true, supported: true, canStop: 'yes', processes: [p, { pid: 'x' }, { ...p, program: 7 }] });
  assert.deepEqual(r.ok && [r.value.canStop, r.value.processes.length], [false, 1]);
  // a member's row has no command: the program name is shown; an admin's shows the masked command
  assert.equal(processLabel(p), 'node vite');
  assert.equal(processLabel({ ...p, command: 'node vite --token=***' }), 'node vite --token=***');
  const a = parseProcesses({ ok: true, canStop: true, processes: [] });
  assert.deepEqual(a.ok && [a.value.supported, a.value.canStop], [true, true]);
  assert.equal(parseProcesses({ ok: false, error: 'x' }).ok, false);
  const page_ = page();
  assert.match(page_, /\{canStop && \(/);
  assert.match(page_, /<ConfirmButton label="Stop" consequence=\{stopConsequence\(p\)\}/);
  assert.match(page_, /p\.agent \? <AgentLink id=\{p\.agent\} name=\{nameOf\(p\.agent\)\} \/> : 'No agent'/);
  assert.match(page_, /title=\{label\}/);
  assert.match(page_, /stopProcess\(p\)/);
  assert.match(page_, /startedText\(p\.startedAt, zone\)/);
});

test('process words: command shortened, ports, start time in the install\'s zone, the confirm', () => {
  assert.equal(shortCommand('node   vite  --port 5173'), 'node vite --port 5173');
  const long = `node ${'x'.repeat(100)}`;
  assert.equal(shortCommand(long, 20).length, 20);
  assert.ok(shortCommand(long, 20).endsWith('…'));
  assert.equal(portsText([5173]), 'Port 5173');
  assert.equal(portsText([3000, 3001]), 'Ports 3000, 3001');
  assert.equal(portsText([]), '');
  const now = new Date('2026-11-03T20:00:00Z');
  assert.match(startedText('2026-11-03T19:13:27Z', 'America/New_York', now), /^Started today 2:13 PM EST$/);
  assert.match(startedText('2026-11-01T19:13:27Z', 'UTC', now), /^Started Nov 1, 7:13 PM UTC$/);
  assert.equal(startedText('nope', 'UTC', now), '');
  assert.match(startedText('2026-11-03T19:13:27Z', 'Not/AZone', now), /^Started /);
  assert.equal(stopConsequence({ program: 'node vite', ports: [5173] }), 'Stop node vite on port 5173? Anything using it stops working.');
  // an admin's confirm names the script the server guessed (detail), a member's row only ever has the program
  assert.equal(stopConsequence({ program: 'node', detail: 'node vite.js', ports: [5173] }), 'Stop node vite.js on port 5173? Anything using it stops working.');
});

test('stop: posts pid, start time and the command hash (never a command); the warning and the refusal are shown', async () => {
  let sent: { url: string; init: RequestInit } | null = null;
  const ok = (async (url: string, init: RequestInit) => { sent = { url, init }; return new Response(JSON.stringify({ ok: true, result: 'stopped', warning: 'Stopped, but something restarted it on :5173 (started by npm)' })); }) as unknown as typeof fetch;
  assert.deepEqual(await stopProcess({ pid: 5, startTicks: 9, commandHash: 'abc' }, ok),
    { ok: true, result: 'stopped', warning: 'Stopped, but something restarted it on :5173 (started by npm)' });
  assert.equal(sent?.url, '/api/settings/agents/processes/stop');
  assert.deepEqual(JSON.parse(String(sent?.init.body)), { pid: 5, startTicks: 9, commandHash: 'abc' });
  const refused = (async () => new Response(JSON.stringify({ ok: false, error: 'Only an admin can stop a program' }), { status: 403 })) as unknown as typeof fetch;
  assert.deepEqual(await stopProcess({ pid: 5, startTicks: 9, commandHash: 'x' }, refused), { ok: false, error: 'Only an admin can stop a program' });
  assert.deepEqual(await stopProcess({ pid: 5, startTicks: 9 }, ok), { ok: false, error: 'Only an admin can stop a program' });
});
