/**
 * FIX 2 (gm addendum) — a RETIRED registry record must not surface as a down agent.
 *
 * THE MECHANISM, as measured rather than assumed. Every lineage rotation leaves
 * a `seat-gN` row behind. For `gm-g2` and `build-g2` the registry says
 * status=retired, but state/<id>.json is the frozen spawn stub and still says
 * status=spawning. The route builds each row as `{...explicit, ...def, ...state}`,
 * so the stub wins over the registry; applyIdentityPrecedence's grace branch then
 * ages that stale 'spawning' into 'offline'. Net effect: a seat that was
 * deliberately decommissioned reported as a down agent to every client.
 *
 * That is what rooted the org chart at the retired gm-g2 and made the header
 * count two phantom down agents. Verified end to end against the real registry
 * before and after: both ghosts read status 'offline' before, 'retired' after,
 * with live seats unchanged.
 *
 * NOTE for whoever reads this next: the sibling suite
 * tests/agents-identity-precedence.test.ts is NOT matched by package.json's
 * `src/**` + '/' + '*.test.ts' glob, so it has never run under `npm test`. These
 * cases live under src/ deliberately, so they actually execute. Fixing that glob
 * is a tooling change and does not ride along on this branch.
 *
 * Run: cd api && npx tsx --test src/routes/agents-identity.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { applyIdentityPrecedence, SPAWNING_GRACE_MS, baseAgentId } from './agents-identity.js';

/** Mirrors the route's `{...explicit, ...def, ...state}` construction. */
function rowAfterSpread(
  def: Record<string, unknown>,
  state: Record<string, unknown>,
  id = 'build-g2',
) {
  return {
    id,
    tier: 'T1',
    name: id,
    machine: 'local',
    tmux_session: id,
    always_on: false,
    status: 'unknown',
    ...def,
    ...state,
  } as Record<string, unknown>;
}

const STALE_STUB = {
  status: 'spawning',
  spawned_at: new Date(Date.now() - SPAWNING_GRACE_MS - 60_000).toISOString(),
};

// ── the ghost rows rotation leaves behind ────────────────────────────────

// MUTATION: delete the retired/archived branch in applyIdentityPrecedence and
// this goes red with status 'offline' — the exact value the API served before.
test('retired registry record beats a stale spawning stub, and is not offline', () => {
  const agent = rowAfterSpread({ status: 'retired', tier: 'T1' }, STALE_STUB);
  assert.equal(agent.status, 'spawning', 'precondition: the stub really does win the spread');

  applyIdentityPrecedence(agent, { status: 'retired', tier: 'T1' }, 'build-g2', 'build-g2', 'local', false);

  assert.equal(agent.status, 'retired');
  assert.equal(agent.retired, true);
  assert.notEqual(agent.status, 'offline', 'retired is not down — that is the whole bug');
  assert.notEqual(agent.status, 'spawning', 'and it is not still booting either');
});

test('archived is treated the same as retired', () => {
  const agent = rowAfterSpread({ status: 'archived' }, STALE_STUB, 'old-seat');
  applyIdentityPrecedence(agent, { status: 'archived' }, 'old-seat', 'old-seat', 'local', false);
  assert.equal(agent.status, 'retired');
  assert.equal(agent.retired, true);
});

test('retirement is keyed off registry status, not off a -gN id shape', () => {
  // The next ghost will not necessarily be named `seat-gN`. Two controls:
  // a retired seat with an ordinary name, and a LIVE seat that does have -g2.
  const plain = rowAfterSpread({ status: 'retired' }, STALE_STUB, 'kai-assistant');
  applyIdentityPrecedence(plain, { status: 'retired' }, 'kai-assistant', 'kai-assistant', 'local', false);
  assert.equal(plain.retired, true, 'no -gN suffix, still retired');

  const liveG2 = rowAfterSpread({ status: 'online' }, { status: 'working' }, 'think-g2');
  applyIdentityPrecedence(liveG2, { status: 'online' }, 'think-g2', 'think-g2', 'local', true);
  assert.equal(liveG2.retired, false, 'a -gN id alone must never mean retired');
  assert.equal(liveG2.status, 'working');
});

test('a retired record still reports WHY, so a debug view is not blank', () => {
  const agent = rowAfterSpread({ status: 'retired' }, STALE_STUB);
  applyIdentityPrecedence(agent, { status: 'retired' }, 'build-g2', 'build-g2', 'local', false);
  assert.match(String(agent.activity), /decommissioned/i);
});

// ── the must-preserve half: a genuinely down LIVE agent is still down ─────
// This is tonight's earlier fix (af199c1). If the retirement branch ever gets
// broadened, this is what catches it.

test('a genuinely down LIVE agent is still offline, not retired', () => {
  const agent = rowAfterSpread({ status: 'online' }, STALE_STUB, 'ship');
  applyIdentityPrecedence(agent, { status: 'online' }, 'ship', 'ship', 'local', false);
  assert.equal(agent.retired, false);
  assert.equal(agent.status, 'offline', 'stale stub + not alive still ages into offline');
});

test('a live agent past the grace window becomes idle, not retired', () => {
  const agent = rowAfterSpread({ status: 'online' }, STALE_STUB, 'plan');
  applyIdentityPrecedence(agent, { status: 'online' }, 'plan', 'plan', 'local', true);
  assert.equal(agent.retired, false);
  assert.equal(agent.status, 'idle');
});

test('a genuinely fresh spawn is left alone inside the grace window', () => {
  const fresh = { status: 'spawning', spawned_at: new Date(Date.now() - 1000).toISOString() };
  const agent = rowAfterSpread({ status: 'provisioning' }, fresh, 'builder-1');
  applyIdentityPrecedence(agent, { status: 'provisioning' }, 'builder-1', 'builder-1', 'local', true);
  assert.equal(agent.status, 'spawning', 'still booting — must not be aged out early');
  assert.equal(agent.retired, false);
});

// ── identity precedence itself must survive the new early return ──────────
// The retired branch returns early. Everything the law guarantees has to be
// assigned BEFORE it, or retiring a seat would blank its name and tier.

test('a retired record keeps its registry identity despite the early return', () => {
  const def = { name: 'Build', tier: 'T1', machine: 'mac', always_on: true, status: 'retired' };
  const agent = rowAfterSpread(def, { ...STALE_STUB, name: '', tier: 'T2' });
  assert.equal(agent.name, '', 'precondition: the blank-name stub won the spread');

  applyIdentityPrecedence(agent, def, 'build-g2', 'build-g2', 'mac', false);

  assert.equal(agent.name, 'Build', 'identity still flows down from the registry');
  assert.equal(agent.tier, 'T1');
  assert.equal(agent.machine, 'mac');
  assert.equal(agent.tmux_session, 'build-g2');
  assert.equal(agent.always_on, true);
  assert.equal(agent.status, 'retired');
});

/**
 * The lineage/history lookups key on a CANONICAL ROOT, but a discovery row's id carries a
 * prefix (`unregistered:<session>`), and the mac gateway's rows carry `mac:`. Two lookups in
 * agents.ts have to strip that prefix to hit a root: the client_description map, which already
 * did it inline, and the generations_total count added in #147, which did NOT — so a prefixed
 * row silently lost its history chip while the row beside it got a description. One helper,
 * used by both, so they cannot drift apart again.
 *
 * Anchored at the START of the id on purpose: the inline `.replace('mac:', '')` it replaces was
 * unanchored and non-global, so it would have eaten `mac:` from the middle of a session name.
 */
test('baseAgentId strips only a leading discovery prefix', () => {
  assert.equal(baseAgentId('gm'), 'gm', 'a plain root is unchanged');
  assert.equal(baseAgentId('unregistered:some-session'), 'some-session');
  assert.equal(baseAgentId('mac:ios-watch-dev'), 'ios-watch-dev');
  // Not a prefix -> left alone. An id is a lookup key, not prose to be rewritten.
  assert.equal(baseAgentId('build-mac:2'), 'build-mac:2', 'mid-string match is not a prefix');
  assert.equal(baseAgentId('agent-unregistered:x'), 'agent-unregistered:x');
  // Only one prefix is stripped, and only from the front.
  assert.equal(baseAgentId('mac:mac:x'), 'mac:x');
});
