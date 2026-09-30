import assert from 'node:assert';
import { test } from 'node:test';
import { searchFleet, flattenVisibleHits } from './agentSearch.ts';

const agents = [
  { id: 'build', name: 'build', tier: 'T1', system_prompt: 'prompts/build.md' },
  { id: 'builder-1', name: 'builder-1', tier: 'T2', system_prompt: 'prompts/build.md' },
  { id: 'builder-2', name: 'builder-2', tier: 'T2', system_prompt: 'prompts/build.md' },
  { id: 'gm', name: 'gm', role: 'lead', tier: 'T0', system_prompt: 'prompts/gm.md' },
  { id: 'think', name: 'Thinker', tier: 'T2', system_prompt: 'prompts/think.md' },
];

const repos = [
  { slug: 'duelo', name: 'duelo-de-dibujo', repo: '/Users/flybyflow/duelo-de-dibujo', agents: [{ id: 'build' }, { id: 'think' }] },
  { slug: 'orchestraos', name: 'orchestraos', repo: '/Users/flybyflow/orchestraos', agents: [{ id: 'gm' }] },
];

test('ranking: agents group comes first, then repos, prompts, files', () => {
  const result = searchFleet('build', { agents, repos });
  assert.deepStrictEqual(Object.keys(result), ['agents', 'repos', 'prompts', 'files']);
});

test('empty query returns nothing, not everything', () => {
  assert.deepStrictEqual(searchFleet('', { agents, repos }), { agents: [], repos: [], prompts: [], files: [] });
  assert.deepStrictEqual(searchFleet('   ', { agents, repos }), { agents: [], repos: [], prompts: [], files: [] });
  // Added during integration (build): a PADDED query must still match. Without this, dropping
  // the .trim() before matching survives every other assertion here — '   ' returns empty
  // either way because no label contains three spaces — while a pasted '  gm  ' silently
  // finds nothing. Pasted-with-whitespace is a real input, not a synthetic edge case.
  assert.ok(searchFleet('  build  ', { agents, repos }).agents.length > 0,
    'a query with surrounding whitespace must be trimmed, not matched literally');
});

test('case-insensitive substring match', () => {
  const result = searchFleet('BUILD', { agents, repos });
  assert.ok(result.agents.some((h) => h.id === 'build'));
  assert.ok(result.agents.some((h) => h.id === 'builder-1'));
});

test('agentIds mapping: repo hits carry the project\'s agent ids', () => {
  const result = searchFleet('duelo', { agents, repos });
  assert.strictEqual(result.repos.length, 1);
  assert.deepStrictEqual(result.repos[0].agentIds, ['build', 'think']);
});

test('agentIds mapping: prompt hits group agents sharing a system_prompt path', () => {
  const result = searchFleet('build.md', { agents, repos });
  assert.strictEqual(result.prompts.length, 1);
  assert.deepStrictEqual(result.prompts[0].agentIds.sort(), ['build', 'builder-1', 'builder-2']);
});

test('files category is always empty (no reachable data source)', () => {
  const result = searchFleet('anything', { agents, repos });
  assert.deepStrictEqual(result.files, []);
});

test('agents group is files scoped out even with no repos provided', () => {
  const result = searchFleet('build', { agents });
  assert.deepStrictEqual(result.repos, []);
});

test('stable order within the agents group: id match ranks before name-only match', () => {
  const pool = [
    { id: 'zeta', name: 'contains-echo-in-name' },
    { id: 'echo', name: 'alpha' },
  ];
  const result = searchFleet('echo', { agents: pool });
  assert.deepStrictEqual(result.agents.map((h) => h.id), ['echo', 'zeta']);
});

test('stable order within the agents group: alphabetical tiebreak on equal rank', () => {
  const pool = [
    { id: 'zzz-agent', name: 'zzz' },
    { id: 'aaa-agent', name: 'aaa' },
  ];
  const result = searchFleet('agent', { agents: pool });
  assert.deepStrictEqual(result.agents.map((h) => h.id), ['aaa-agent', 'zzz-agent']);
});

test('flattenVisibleHits orders agents, then repos, then prompts, and excludes files', () => {
  const result = searchFleet('build', { agents, repos });
  const flat = flattenVisibleHits(result);
  assert.deepStrictEqual(flat.map((h) => h.kind), [
    ...result.agents.map(() => 'agent'),
    ...result.repos.map(() => 'repo'),
    ...result.prompts.map(() => 'prompt'),
  ]);
  assert.strictEqual(flat.length, result.agents.length + result.repos.length + result.prompts.length);
});
