/**
 * R3 precedence law (visibility audit D1, gm-g47 build-tier #1) — RED-first.
 *
 * LAW: identity fields (name, tier, machine, tmux_session, always_on) flow DOWN
 * from DB/registry (`def`) ONLY. A per-agent state/<id>.json contributes RUNTIME
 * fields only (status, current_task, last_updated, ...). A spawn-time stub
 * ({name:'', tier:'T2', status:'spawning'} written by spawn-agent.sh:684-708 and
 * never updated) must never clobber identity — that stub blanked
 * topography-research-dev off the :8890 web page on 2026-09-06 while iOS
 * (watch-gateway, which never reads these files) showed it fine.
 *
 * Also: a stuck 'spawning' status is freshness-gated — older than the grace
 * window it derives from liveness instead of lying forever.
 *
 * Run: cd api && npx tsx --test tests/agents-identity-precedence.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { applyIdentityPrecedence, SPAWNING_GRACE_MS } from '../src/routes/agents-identity.js';

const DEF = { name: 'topography-research-dev', tier: 'T3', machine: 'vps', always_on: true };
const ID = 'topography-research-dev';
const TMUX = 'topography-research-dev';

function agentAfterSpread(state: Record<string, unknown>) {
  // Mirrors the route's construction: explicit fields, then ...def, ...state —
  // i.e. the state file has already clobbered whatever it carries.
  return {
    id: ID,
    tier: 'T3',
    name: ID,
    machine: 'vps',
    tmux_session: TMUX,
    always_on: true,
    status: 'idle',
    current_task: null as string | null,
    last_updated: null as string | null,
    tmux_alive: true,
    inbox_count: 0,
    machine_status: 'online',
    ...DEF,
    ...state,
  } as Record<string, unknown>;
}

test('blank-name spawn stub cannot clobber identity (the topography incident)', () => {
  const state = { name: '', tier: 'T2', status: 'spawning', spawned_at: '2026-09-05T16:56:20' };
  const agent = agentAfterSpread(state);
  assert.equal(agent.name, '', 'precondition: spread DID clobber (bug reproduced)');
  applyIdentityPrecedence(agent, DEF, ID, TMUX, 'vps', true);
  assert.equal(agent.name, 'topography-research-dev');
  assert.equal(agent.tier, 'T3');
  assert.equal(agent.machine, 'vps');
  assert.equal(agent.tmux_session, TMUX);
  assert.equal(agent.always_on, true);
});

test('identity flows DOWN only: even a NON-empty state-file name loses to def', () => {
  const state = { name: 'some-drifted-name', tier: 'T9' };
  const agent = agentAfterSpread(state);
  applyIdentityPrecedence(agent, DEF, ID, TMUX, 'vps', true);
  assert.equal(agent.name, 'topography-research-dev');
  assert.equal(agent.tier, 'T3');
});

test('def with no name falls back to id, never empty string', () => {
  const state = { name: '' };
  const agent = agentAfterSpread(state);
  applyIdentityPrecedence(agent, { name: '' }, ID, TMUX, 'vps', true);
  assert.equal(agent.name, ID);
});

test('stale spawning status derives from liveness (alive -> idle)', () => {
  const old = new Date(Date.now() - SPAWNING_GRACE_MS - 60_000).toISOString();
  const agent = agentAfterSpread({ status: 'spawning', spawned_at: old });
  applyIdentityPrecedence(agent, DEF, ID, TMUX, 'vps', true);
  assert.equal(agent.status, 'idle');
});

test('stale spawning status derives from liveness (dead -> offline)', () => {
  const old = new Date(Date.now() - SPAWNING_GRACE_MS - 60_000).toISOString();
  const agent = agentAfterSpread({ status: 'spawning', spawned_at: old });
  applyIdentityPrecedence(agent, DEF, ID, TMUX, 'vps', false);
  assert.equal(agent.status, 'offline');
});

test('spawning with NO spawned_at is treated as stale, not trusted forever', () => {
  const agent = agentAfterSpread({ status: 'spawning' });
  applyIdentityPrecedence(agent, DEF, ID, TMUX, 'vps', true);
  assert.equal(agent.status, 'idle');
});

test('fresh spawning inside the grace window is kept', () => {
  const fresh = new Date(Date.now() - 30_000).toISOString();
  const agent = agentAfterSpread({ status: 'spawning', spawned_at: fresh });
  applyIdentityPrecedence(agent, DEF, ID, TMUX, 'vps', true);
  assert.equal(agent.status, 'spawning');
});

test('runtime fields from the state file still flow through untouched', () => {
  const agent = agentAfterSpread({ status: 'working', current_task: 'building X', last_updated: '2026-09-06T10:00:00Z' });
  applyIdentityPrecedence(agent, DEF, ID, TMUX, 'vps', true);
  assert.equal(agent.status, 'working');
  assert.equal(agent.current_task, 'building X');
  assert.equal(agent.last_updated, '2026-09-06T10:00:00Z');
});
