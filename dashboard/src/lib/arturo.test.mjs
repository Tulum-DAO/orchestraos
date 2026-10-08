/**
 * Pure-logic tests for the Arturo client helpers (G15 boot window + context line).
 *   node --experimental-strip-types dashboard/src/lib/arturo.test.mjs
 */
import assert from 'node:assert';
import { isStarting, waitForArturo, contextLine, contextFromLocation, brainLabel, slugify, firstStep, onboardingTurn, toggleChoice, onboardingDone, DICTATE_TITLE, HANDS_FREE, handsFreeTitle, handsFreeReady, isPageOpener, ONBOARDING_OPENER } from './arturo.ts';

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
// The brain runs onboarding from its instructions; the page only marks the turns and opens it.
assert.equal(onboardingTurn('onboarding', 'hi my name is Shaw nice to meet you'), '[Onboarding: step=onboarding]\nhi my name is Shaw nice to meet you');
assert.equal(onboardingTurn('onboarding_open', ONBOARDING_OPENER), `[Onboarding: step=onboarding_open]\n${ONBOARDING_OPENER}`);
// The page's openers are never shown as the operator's words, including the two older ones on reload.
assert.equal(isPageOpener(ONBOARDING_OPENER), true);
assert.equal(isPageOpener('Introduce my team.'), true);
assert.equal(isPageOpener('Explain how seats are organised here.'), true);
assert.equal(isPageOpener('introduce my team please'), false);
assert.equal(isPageOpener('hi'), false);
// A multi-select card the brain wrote: card order, a second tap un-picks, an exclusive option stays exclusive.
{
  const O = ['iPhone', 'iPad', 'Apple Watch', 'Mac', 'None of these'], X = 'None of these';
  let p = toggleChoice(O, [], 'Apple Watch');
  p = toggleChoice(O, p, 'iPhone');
  assert.deepEqual(p, ['iPhone', 'Apple Watch']);
  assert.deepEqual(toggleChoice(O, p, 'iPhone'), ['Apple Watch']);
  assert.deepEqual(toggleChoice(O, p, X, X), [X]);
  assert.deepEqual(toggleChoice(O, [X], 'Mac', X), ['Mac']);
}
console.log('arturo.test.mjs: onboarding helpers ok');

// --- message states: one vocabulary for every Arturo chatmode ---------------------------
import { sendStateLabel } from './arturo.ts';
assert.equal(sendStateLabel('sending'), 'Sending…');
assert.equal(sendStateLabel('sent'), 'Sent');
assert.equal(sendStateLabel('acked'), 'Acknowledged');
assert.equal(sendStateLabel('failed'), 'Not delivered');
assert.equal(sendStateLabel(undefined), '');
console.log('arturo.test.mjs: send states ok');

// --- onboardingDone: the page leaves onboarding only on the server's flag (S3, review of #278) ---------
assert.equal(onboardingDone({ ok: true, onboarding: { done: true } }), true);
assert.equal(onboardingDone({ ok: true, onboarding: { done: false } }), false);
assert.equal(onboardingDone({ ok: true }), false);                                  // a turn off the onboarding says nothing
assert.equal(onboardingDone({ ok: false, onboarding: { done: true } }), false);     // a failed turn never ends it
assert.equal(onboardingDone({ ok: true, onboarding: { done: 'yes' } }), false);     // only the boolean
assert.equal(onboardingDone(null), false);

// --- voice controls say what each one is (the operator, 2026-10-08) --------------------------------------
{
  assert.match(DICTATE_TITLE, /browser's mic permission/);                       // what they already have
  assert.equal(HANDS_FREE, 'Live voice mode');                                 // the operator's name (apr_4e479200)
  assert.match(handsFreeTitle(true), /talks back/);
  assert.match(handsFreeTitle(false), /needs a voice key/);
  assert.match(handsFreeTitle(false), /GEMINI_API_KEY/);                       // the key the browser call uses
  assert.equal(handsFreeReady({ live: true, voice: true }), true);
  assert.equal(handsFreeReady({ live: false, voice: true }), false);          // an ElevenLabs key alone cannot start it
  assert.equal(handsFreeReady({ voice: true }), true);                        // an older server: any key
  assert.equal(handsFreeReady(null), false);
  // A bare "Voice mode" named neither dictation nor the call, and dictation is never a mode or something to
  // approve. The operator's own name for the call, "Live voice mode", is fine.
  const BANNED = /(?<!live )voice mode|conversation mode|dictation mode/i;
  assert.equal(BANNED.test('aria-label="Voice mode"'), true);                   // sabotage: the old label fails
  assert.equal(BANNED.test('Turn on dictation mode'), true);
  // the browser's own permission prompt IS the gate, so telling them to allow the mic there is fine
  assert.equal(BANNED.test('microphone permission was denied — allow the mic for this site and tap again'), false);
  assert.equal(BANNED.test('Live voice mode'), false);                          // ...and Shaw's name passes
  assert.equal(BANNED.test(HANDS_FREE), false);
  const { readdirSync, readFileSync, statSync } = await import('node:fs');
  const { join } = await import('node:path');
  const root = new URL('..', import.meta.url).pathname;
  const hits = [];
  const walk = (d) => { for (const f of readdirSync(d)) { const p = join(d, f); if (statSync(p).isDirectory()) walk(p);
    else if (/\.(tsx|ts)$/.test(f) && !/\.test\./.test(f) && BANNED.test(readFileSync(p, 'utf8'))) hits.push(p); } };
  walk(root);
  assert.deepEqual(hits, []);
}
