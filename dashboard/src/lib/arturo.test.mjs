/**
 * Pure-logic tests for the Arturo client helpers (G15 boot window + context line).
 *   node --experimental-strip-types dashboard/src/lib/arturo.test.mjs
 */
import assert from 'node:assert';
import { isStarting, waitForArturo, contextLine, contextFromLocation, brainLabel, slugify, firstStep, stepAfterRuntime, onboardingTurn, teamStepDone, toggleChoice, devicesAnswer, DEVICE_OPTIONS, DEVICE_ONLY_HERE } from './arturo.ts';

// --- isStarting: boot-window errors are "starting", real errors are not ------------------
assert.equal(isStarting({ ok: false, error: 'HTTP 502' }), true);
assert.equal(isStarting({ ok: false, error: 'HTTP 503' }), true);
assert.equal(isStarting({ ok: false, error: 'arturo unreachable' }), true);
assert.equal(isStarting({ ok: false, error: 'gateway unreachable' }), true);
assert.equal(isStarting({ ok: false, error: 'Failed to fetch' }), true);
assert.equal(isStarting({ ok: false, status: 502, error: 'gateway token unavailable' }), true);   // API before the gateway wrote its token
assert.equal(isStarting({ ok: false, status: 502, error: 'anything at all' }), true);
assert.equal(isStarting({ ok: false, error: 'HTTP 401' }), false);
assert.equal(isStarting({ ok: false, error: 'HTTP 500' }), false);
assert.equal(isStarting({ ok: true }), false);
assert.equal(isStarting(null), false);

// --- waitForArturo: polls until ok, backs off, stops at maxMs --------------------------------
{
  const answers = [{ ok: false, error: 'HTTP 502' }, { ok: false, error: 'arturo unreachable' }, { ok: true, brain: { kind: 'runtime', model: 'x' } }];
  let i = 0;
  globalThis.fetch = async () => {
    const a = answers[Math.min(i++, answers.length - 1)];
    return { ok: a.ok, status: a.ok ? 200 : 502, json: async () => a };
  };
  const ticks = [];
  const t0 = Date.now();
  const h = await waitForArturo({ maxMs: 20000, onTick: (_h, n) => ticks.push(n) });
  assert.equal(h.ok, true);
  assert.deepEqual(ticks, [1, 2]);                     // two starting answers, then ready
  assert.ok(Date.now() - t0 >= 3000, 'backoff 1s + 2s happened');
}
{
  globalThis.fetch = async () => ({ ok: false, status: 502, json: async () => ({ ok: false, error: 'HTTP 502' }) });
  const t0 = Date.now();
  const h = await waitForArturo({ maxMs: 1500 });
  assert.equal(h.ok, false);
  assert.ok(Date.now() - t0 < 6000, 'gives up near maxMs');
}
{
  // a non-starting error returns immediately (401 is not a boot window)
  globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({ ok: false, error: 'unauthorized' }) });
  const t0 = Date.now();
  const h = await waitForArturo({ maxMs: 20000 });
  assert.equal(h.ok, false); assert.ok(Date.now() - t0 < 500);
}

// --- context record + line -------------------------------------------------------------------
assert.equal(contextLine(null), '');
assert.equal(contextLine({ route: '/approvals', entityKind: 'approvals', entityId: 'apr_1' }), '[Context: route=/approvals entity=approvals:apr_1]');
assert.deepEqual(contextFromLocation('/agent/gm', { id: 'gm' }, ''), { route: '/agent/gm', entityKind: 'agent', entityId: 'gm' });
assert.equal(contextFromLocation('/approvals', {}, '?id=apr_9').entityId, 'apr_9');

// --- labels + seat slug ----------------------------------------------------------------------
assert.equal(brainLabel({ kind: 'none', model: 'none' }), 'no brain');
assert.equal(brainLabel({ kind: 'runtime', runtime: 'claude', model: 'claude-cli-default' }), 'Claude');
assert.equal(brainLabel({ kind: 'runtime', runtime: 'claude', model: 'claude-haiku-4-5-20251001' }), 'Haiku 4.5');
assert.equal(slugify('write a haiku about tmux and print it'), 'write-haiku-tmux');

console.log('arturo.test.mjs: all assertions passed');

// --- onboarding is decided by what the SERVER knows; no surface parses a name ----------------
assert.equal(firstStep(true), 'done');
assert.equal(firstStep(false), 'runtime');          // a brain must exist before it is asked to listen (even when a name is cached)
assert.equal(stepAfterRuntime(null, true), 'name');       // server knows no name -> ask (via the brain)
assert.equal(stepAfterRuntime('Shaw', true), 'voice');    // known name -> never asked twice
assert.equal(stepAfterRuntime('Shaw', false), 'team');
// The team step ends on what the SERVER sees after the turn, never on a tool's name.
assert.equal(teamStepDone({ state: 'present' }), true);
assert.equal(teamStepDone({ state: 'other_manager', manager: 'boss' }), true);   // nothing it may create
assert.equal(teamStepDone({ state: 'unknown' }), true);                          // could not check: explain, never offer
assert.equal(teamStepDone({ state: 'absent' }), false);                          // still to ask
assert.equal(teamStepDone({ state: 'incomplete' }), false);                      // a partial start can be asked again
assert.equal(teamStepDone({ state: 'starting' }), false);                        // still running: ask again in a minute
assert.equal(teamStepDone({ state: 'absent', declined: true }), true);           // an explicit no ends it
assert.equal(onboardingTurn('team_open', 'Introduce my team.'), '[Onboarding: step=team_open]\nIntroduce my team.');
// The devices card: multi-select, in the card's order, with "Just this computer" exclusive.
{
  const O = DEVICE_OPTIONS, X = DEVICE_ONLY_HERE;
  let p = toggleChoice(O, [], 'Apple Watch', X);
  p = toggleChoice(O, p, 'iPhone', X);
  assert.deepEqual(p, ['iPhone', 'Apple Watch']);                 // card order, not tap order
  assert.deepEqual(toggleChoice(O, p, 'iPhone', X), ['Apple Watch']);   // a second tap un-picks
  assert.deepEqual(toggleChoice(O, p, X, X), [X]);                 // the exclusive one clears the rest
  assert.deepEqual(toggleChoice(O, [X], 'Mac', X), ['Mac']);       // and any other clears it
  assert.deepEqual(toggleChoice(O, [X], X, X), []);
  assert.equal(devicesAnswer(['iPhone', 'Apple Watch']), 'My devices: iPhone, Apple Watch');
  assert.equal(onboardingTurn('devices', 'My devices: Mac'), '[Onboarding: step=devices]\nMy devices: Mac');
}
assert.equal(teamStepDone(undefined), false);                                    // an older server sends no state
assert.equal(onboardingTurn('team', 'Introduce my team.'), '[Onboarding: step=team]\nIntroduce my team.');
assert.equal(onboardingTurn('name', 'hi my name is Shaw nice to meet you'), '[Onboarding: step=name]\nhi my name is Shaw nice to meet you');
console.log('arturo.test.mjs: onboarding helpers ok');

// --- message states: one vocabulary for every Arturo chatmode ---------------------------
import { sendStateLabel } from './arturo.ts';
assert.equal(sendStateLabel('sending'), 'Sending…');
assert.equal(sendStateLabel('sent'), 'Sent');
assert.equal(sendStateLabel('acked'), 'Acknowledged');
assert.equal(sendStateLabel('failed'), 'Not delivered');
assert.equal(sendStateLabel(undefined), '');
console.log('arturo.test.mjs: send states ok');
