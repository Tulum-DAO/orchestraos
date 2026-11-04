/**
 * liveSwitch.test.ts — the live-switch sequencer (lib/liveSwitch.ts) and its per-agent store + hook
 * (hooks/useLiveSwitch.ts).
 *
 * The reducer first: one request in flight per agent, a single waiting slot where the latest pick wins, a model change
 * and an effort never sent in one PUT, the model an effort goes with resolved when it is sent, the newest outcome alone
 * speaking (a failure earlier in the chain surfaces), a model chain always re-read. Then the real hook, driven through
 * React 19's hook dispatcher with a fake switch whose answers arrive in an order the test chooses, including two pickers
 * mounted on one agent.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as React from 'react';
import {
  idleSwitch, liveSwitchStep, shownOf, RESYNC_AFTER_SWITCH_MS, TURN_END_READ_MS, TURN_POLL_MS, TURN_POLL_MAX,
  type LiveSwitchEffect, type LiveSwitchEvent, type LiveSwitchState, type ReadInfo,
} from './liveSwitch.ts';
import { switchAgentModel, switchNotice, effortNotice, type SwitchBody } from './agentModel.ts';
import { useLiveSwitch, type LiveSwitchDeps } from '../hooks/useLiveSwitch.ts';

const O = { id: 'opus', label: 'Opus' };
const A = { id: 'sonnet', label: 'Sonnet' };
const B = { id: 'haiku', label: 'Haiku' };
const HIGH = { id: 'high', label: 'High' };
const MAX = { id: 'max', label: 'Max' };
const LOW = { id: 'low', label: 'Low' };

type Send = Extract<LiveSwitchEffect, { kind: 'send' }>;
/** Feed events through the reducer, collecting every effect. */
function run(events: LiveSwitchEvent[], from: LiveSwitchState = idleSwitch()) {
  let s = from;
  const out: LiveSwitchEffect[] = [];
  for (const e of events) { const r = liveSwitchStep(s, e); s = r.state; out.push(...r.effects); }
  const sends = out.filter((e) => e.kind === 'send') as Send[];
  const reads = out.filter((e) => e.kind === 'resync') as Extract<LiveSwitchEffect, { kind: 'resync' }>[];
  return { s, out, sends, reads, last: sends[sends.length - 1] };
}
const pick = (intent: { model?: typeof O; effort?: typeof HIGH }, base: string | null = 'opus'): LiveSwitchEvent =>
  ({ type: 'pick', intent, baseModel: base });
const ok = (status = 200, extra: SwitchBody = {}): SwitchBody => ({ ok: status === 200 || status === 202, ...extra });
const reply = (seq: number, status: number, body: SwitchBody = ok(status), base: string | null = 'opus'): LiveSwitchEvent =>
  ({ type: 'reply', seq, status, body, baseModel: base });
const read = (token: number, info: ReadInfo | null, later = false): LiveSwitchEvent => ({ type: 'read', token, info, later });
const pair = (x: Send) => [x.model, x.effort];

test('reducer: a pick from idle goes out at once, shows at once, and the notice names it', () => {
  const m = run([pick({ model: A })]);
  assert.deepEqual(m.sends.map(pair), [['sonnet', undefined]]);
  assert.deepEqual(shownOf(m.s), { model: 'sonnet' });
  assert.deepEqual(m.s.notice, { tone: 'wait', text: 'Switching to Sonnet…' });
  const e = run([pick({ effort: HIGH })]);
  assert.deepEqual(e.sends.map(pair), [['opus', 'high']], 'an effort pick goes with the model it is on');
  assert.deepEqual(e.s.notice, { tone: 'wait', text: 'Setting High effort…' });
  const none = run([pick({ effort: HIGH }, null)]);
  assert.equal(none.sends.length, 0, 'no model known: nothing sent');
});

test('reducer: one in flight; picks meanwhile wait, the latest wins (intermediate picks never sent)', () => {
  const r1 = run([pick({ model: A }), pick({ model: B }), pick({ model: O })]);
  assert.equal(r1.sends.length, 1);
  assert.deepEqual(r1.s.waiting, { model: O });
  assert.deepEqual(shownOf(r1.s), { model: 'opus' }, 'shown: the newest intent');
  const r2 = run([reply(1, 200, ok(200, { switching: 'switched' }))], r1.s);
  assert.deepEqual(r2.sends.map(pair), [['opus', undefined]], 'B never went out');
  assert.deepEqual(r2.s.notice, { tone: 'wait', text: 'Switching to Opus…' }, 'a superseded answer says nothing');
  const r3 = run([reply(2, 200, ok(200, { switching: 'switched' }))], r2.s);
  assert.deepEqual(r3.s.notice, { tone: 'ok', text: 'Switched to Opus.' });
  assert.deepEqual(shownOf(r3.s), { model: 'opus' }, 'kept');
  assert.deepEqual(r3.reads, [{ kind: 'resync', delayMs: RESYNC_AFTER_SWITCH_MS, token: 2, later: true }]);
});

test('reducer: only the answer to the request in flight is read (late, duplicate, unknown seq ignored)', () => {
  const a = run([pick({ model: A })]);
  for (const late of [reply(0, 500, { ok: false, error: 'x' }), reply(9, 200)]) {
    const r = liveSwitchStep(a.s, late);
    assert.equal(r.state, a.s);
    assert.deepEqual(r.effects, []);
  }
  const done = run([reply(1, 200, ok(200, { switching: 'switched' }))], a.s);
  assert.equal(liveSwitchStep(done.s, reply(1, 500, { ok: false })).state, done.s);
});

test('C1: a model change and an effort never share a PUT: the model alone, then the effort with the model it reached', () => {
  const r = run([pick({ model: A }), pick({ effort: HIGH }), pick({ model: B }), pick({ effort: MAX })]);
  assert.deepEqual(r.s.waiting, { model: B, effort: MAX }, 'they merge in the waiting slot');
  assert.deepEqual(shownOf(r.s), { model: 'haiku', effort: 'max' });
  const r2 = run([reply(1, 200, ok(200, { switching: 'switched' }))], r.s);
  assert.deepEqual(r2.sends.map(pair), [['haiku', undefined]], 'the model alone');
  assert.deepEqual(r2.s.waiting, { effort: MAX }, 'the effort still waits');
  const r3 = run([reply(2, 202, ok(202, { status: 'pending' }))], r2.s);
  assert.deepEqual(r3.sends.map(pair), [['haiku', 'max']], 'then the effort, with the model that reply reached');
});

test('C1: pick A (202) -> while out pick the current model O (cancel) -> pick effort: ends on O with the effort, cancel kept', () => {
  const r = run([pick({ model: A }), pick({ model: O }), pick({ effort: HIGH })]);
  const r2 = run([reply(1, 202, ok(202, { status: 'pending', model: 'sonnet' }))], r.s);
  assert.deepEqual(r2.sends.map(pair), [['opus', undefined]], 'the cancel goes out as a bare model pick');
  const r3 = run([reply(2, 200, ok(200, { changed: false, cancelled: true, switching: 'switched', model: 'opus' }))], r2.s);
  assert.deepEqual(r3.sends.map(pair), [['opus', 'high']], 'the effort with O, never with A (which would revive A)');
  const r4 = run([reply(3, 200, ok(200, { effort: 'high', effort_switching: 'switched' }))], r3.s);
  assert.deepEqual(shownOf(r4.s), { model: 'opus', effort: 'high' });
  assert.equal(r4.s.notice?.text, 'Kept Opus; the waiting switch was cancelled. Effort set to High.');
});

test('B: the model for an effort pick is resolved when it is sent, from the last model the gateway accepted', () => {
  const r = run([pick({ model: A }), pick({ effort: HIGH })]);
  const okd = run([reply(1, 200, ok(200, { switching: 'switched' }))], r.s);
  assert.deepEqual(okd.sends.map(pair), [['sonnet', 'high']], 'never the old model: that would switch back');
  const pend = run([reply(1, 202, ok(202))], r.s);
  assert.deepEqual(pend.sends.map(pair), [['sonnet', 'high']], 'the model waiting for the turn to end');
  const failed = run([reply(1, 409, { ok: false, error: 'busy' })], r.s);
  assert.deepEqual(failed.sends.map(pair), [['opus', 'high']], 'the switch failed: the model the server read says');
  const fresh = run([reply(1, 409, { ok: false, error: 'busy' }, 'haiku')], r.s);
  assert.deepEqual(fresh.sends.map(pair), [['haiku', 'high']], 'the server read as it is now, not as it was at pick time');
  // After the chain drained, before a read lands: still the model it reached.
  const later = run([reply(2, 200, ok(200)), pick({ effort: LOW }, 'opus')], okd.s);
  assert.deepEqual(later.sends.map(pair), [['sonnet', 'low']]);
});

test('D: a superseded failure surfaces: pick A, pick E, A fails 422, E 200 -> shows O, the failure, and re-reads now', () => {
  const r = run([pick({ model: A }), pick({ effort: HIGH }), reply(1, 422, { ok: false, error: 'not offered for this agent' })]);
  assert.deepEqual(shownOf(r.s), { effort: 'high' }, 'A dropped from what shows at once: the picker shows O again');
  assert.deepEqual(r.s.notice, { tone: 'wait', text: 'Setting High effort…' }, 'names what is in flight');
  const r2 = run([reply(2, 200, ok(200, { effort: 'high', effort_switching: 'switched' }))], r.s);
  assert.deepEqual(r2.s.notice, { tone: 'error', text: "Couldn't switch to Sonnet: not offered for this agent." });
  assert.deepEqual(r2.reads, [{ kind: 'resync', delayMs: 0, token: 2, later: false }]);
  assert.deepEqual(shownOf(r2.s), { effort: 'high' });
  // An effort failure superseded by a model pick likewise.
  const e = run([pick({ effort: MAX }), pick({ model: A }), reply(1, 409, { ok: false, error: 'busy' })]);
  assert.deepEqual(shownOf(e.s), { model: 'sonnet' });
  const e2 = run([reply(2, 200, ok(200, { switching: 'switched' }))], e.s);
  assert.deepEqual(e2.s.notice, { tone: 'error', text: "Couldn't set Max effort: busy." });
  assert.deepEqual(e2.reads.map((x) => x.delayMs).sort(), [0, RESYNC_AFTER_SWITCH_MS], 'now for the failure, later for the switch');
});

test('reducer: failure of the newest re-reads now; the shown value stays until the read lands, nothing guessed', () => {
  const r = run([pick({ model: A }), pick({ model: B }), reply(1, 200, ok(200, { switching: 'switched' })),
    reply(2, 422, { ok: false, error: 'not offered for this agent' })]);
  assert.deepEqual(r.s.notice, { tone: 'error', text: "Couldn't switch to Haiku: not offered for this agent." });
  assert.deepEqual(shownOf(r.s), { model: 'sonnet' }, 'the model that really applied, not the failed one, not the old one');
  assert.ok(r.reads.some((x) => x.delayMs === 0));
  assert.ok(!r.out.some((e) => e.kind === 'applied' && e.model === 'haiku'));
});

test('E: every chain with a model is re-read; a switched one waits for the record; a read clears the remembered model', () => {
  const sw = run([pick({ model: A }), reply(1, 200, ok(200, { switching: 'switched' }))]);
  assert.deepEqual(sw.reads, [{ kind: 'resync', delayMs: RESYNC_AFTER_SWITCH_MS, token: 1, later: true }]);
  const landed = run([read(1, { model: 'sonnet', pending: null }, true)], sw.s);
  assert.deepEqual(shownOf(landed.s), {}, 'the server\'s own values show');
  assert.equal(landed.s.accepted, null);
  const rot = run([pick({ model: A }), reply(1, 200, ok(200, { switching: 'next_rotation' }))]);
  assert.deepEqual(rot.reads.map((x) => x.delayMs), [0]);
  // An effort pick that cancels the scheduled re-read: rescheduled when its chain drains.
  const cut = run([pick({ effort: HIGH })], sw.s);
  assert.ok(cut.out.some((e) => e.kind === 'cancelResync'));
  const cut2 = run([reply(2, 200, ok(200))], cut.s);
  assert.deepEqual(cut2.reads, [{ kind: 'resync', delayMs: RESYNC_AFTER_SWITCH_MS, token: 2, later: true }]);
  // A read made now while a switched model's record may still lag keeps that model shown until the later read.
  const both = run([pick({ model: A }), pick({ effort: MAX }), reply(1, 200, ok(200, { switching: 'switched' })),
    reply(2, 409, { ok: false, error: 'busy' })]);
  const now = run([read(2, { model: 'opus', pending: null })], both.s);
  assert.deepEqual(shownOf(now.s), { model: 'sonnet' }, 'the record has not caught up yet');
  const lat = run([read(2, { model: 'sonnet', pending: null }, true)], now.s);
  assert.deepEqual(shownOf(lat.s), {});
  // Effort-only chains are not re-read.
  assert.deepEqual(run([pick({ effort: HIGH }), reply(1, 200, ok(200))]).reads, []);
});

test('E: 202 is re-read now, and the pick stays shown while the server says it is still waiting', () => {
  const m = run([pick({ model: A }), reply(1, 202, ok(202, { status: 'pending', applies: 'at turn end' }))]);
  assert.deepEqual(m.s.notice, { tone: 'wait', text: 'Sonnet will apply when the agent finishes its current turn.' });
  assert.deepEqual(m.reads.map((x) => x.delayMs), [0]);
  const waiting = run([read(1, { model: 'opus', pending: { model: 'sonnet' } })], m.s);
  assert.deepEqual(shownOf(waiting.s), { model: 'sonnet' }, 'server still pending Sonnet: kept');
  const gone = run([read(1, { model: 'opus', pending: null })], m.s);
  assert.deepEqual(shownOf(gone.s), {}, 'no longer pending: the server\'s model');
  // An effort waiting for the turn to end too.
  const e = run([pick({ effort: HIGH }), reply(1, 202, ok(202))]);
  assert.deepEqual(e.s.notice, { tone: 'wait', text: 'High effort will apply when the agent finishes its current turn.' });
  assert.deepEqual(shownOf(e.s), { effort: 'high' });
});

test('G: a model then an effort both waiting for the turn to end are said together', () => {
  const r = run([pick({ model: A }), pick({ effort: HIGH }), reply(1, 202, ok(202)), reply(2, 202, ok(202))]);
  assert.deepEqual(r.s.notice, { tone: 'wait', text: 'Sonnet and High effort will apply when the agent finishes its current turn.' });
  const mixed = run([pick({ model: A }), pick({ effort: HIGH }), reply(1, 200, ok(200, { switching: 'switched' })),
    reply(2, 200, ok(200, { effort: 'high', effort_switching: 'switched' }))]);
  assert.deepEqual(mixed.s.notice, { tone: 'ok', text: 'Switched to Sonnet. Effort set to High.' });
  // While busy, the notice names what is in flight, not the newest waiting pick.
  const busy = run([pick({ model: A }), pick({ effort: HIGH })]);
  assert.deepEqual(busy.s.notice, { tone: 'wait', text: 'Switching to Sonnet…' });
});

test('reducer: a pick back to what is in flight clears the waiting slot; the same pick twice is nothing', () => {
  const r = run([pick({ model: A }), pick({ model: B }), pick({ model: A })]);
  assert.equal(r.s.waiting, null);
  const done = run([reply(1, 200, ok(200, { switching: 'switched' }))], r.s);
  assert.equal(done.sends.length, 0);
  assert.deepEqual(done.s.notice, { tone: 'ok', text: 'Switched to Sonnet.' });
  const twice = run([pick({ model: A }), pick({ model: A })]);
  assert.equal(twice.sends.length, 1);
  assert.equal(twice.s.waiting, null);
  assert.deepEqual(run([pick({ model: O })]).sends.map(pair), [['opus', undefined]], 'from idle the current model still goes (cancel)');
});

test('reducer: the last picker leaving drops the waiting pick; the request in flight still answers', () => {
  const r = run([pick({ model: A }), pick({ model: B }), { type: 'leave' }]);
  assert.equal(r.s.waiting, null);
  assert.equal(r.s.inFlight?.seq, 1);
  assert.deepEqual(shownOf(r.s), { model: 'sonnet' });
  const done = run([reply(1, 200, ok(200, { switching: 'switched' }))], r.s);
  assert.equal(done.sends.length, 0, 'B never goes out');
});

test('H: "applied" (the panel\'s Model row) only for a confirmed live switch, not 202 or next rotation', () => {
  const applied = (status: number, body: SwitchBody) => run([pick({ model: A }), reply(1, status, body)]).out.filter((e) => e.kind === 'applied');
  assert.deepEqual(applied(200, ok(200, { switching: 'switched' })), [{ kind: 'applied', model: 'sonnet' }]);
  assert.deepEqual(applied(202, ok(202)), []);
  assert.deepEqual(applied(200, ok(200, { switching: 'next_rotation' })), []);
  assert.deepEqual(applied(500, { ok: false }), []);
});

test('F: a switch with no answer in time frees the lane and says it may still go through', async () => {
  const orig = globalThis.fetch;
  globalThis.fetch = ((_u: string, init: RequestInit) => new Promise((_res, rej) => {
    init.signal?.addEventListener('abort', () => rej(init.signal!.reason));
  })) as typeof fetch;
  // Node's AbortSignal.timeout timer does not hold the event loop open; this does, as a page would.
  const keep = setInterval(() => {}, 5);
  try {
    const r = await switchAgentModel('a', 'sonnet', undefined, 20);
    assert.deepEqual(r, { status: 0, body: { error: 'timeout' } });
    assert.deepEqual(switchNotice(r.status, r.body, 'Sonnet'),
      { tone: 'error', text: 'No answer in time. Check the model again in a moment: the switch may still go through.' });
    assert.deepEqual(effortNotice(r.status, r.body, 'High'),
      { tone: 'error', text: 'No answer in time. Check the effort again in a moment: the change may still go through.' });
    assert.equal(switchNotice(0, {}, 'Sonnet').text, "Couldn't reach OrchestraOS. Nothing was switched.");
  } finally { globalThis.fetch = orig; clearInterval(keep); }
  const src = readFileSync(new URL('./agentModel.ts', import.meta.url), 'utf8');
  assert.match(src, /SWITCH_TIMEOUT_MS = 70_000/);
  assert.match(src, /AbortSignal\.timeout\(timeoutMs\)/);
});

test('a model switch whose outcome is unknown (timeout, gateway/proxy unreachable) is re-read now AND after 8 s', () => {
  for (const [status, body] of [[0, { error: 'timeout' }], [502, { ok: false, error: 'gateway unreachable' }],
    [502, { error: 'upstream unreachable', detail: 'socket hang up' }]] as [number, SwitchBody][]) {
    const r = run([pick({ model: A }), reply(1, status, body)]);
    assert.deepEqual(r.reads.map((x) => [x.delayMs, x.later]), [[0, false], [RESYNC_AFTER_SWITCH_MS, true]],
      `${status} ${body.error}: a late success at the gateway is picked up`);
    assert.equal(r.s.notice?.text, 'No answer in time. Check the model again in a moment: the switch may still go through.');
  }
  // A known failure is re-read now only; an unknown effort outcome too (no record lag for an effort).
  assert.deepEqual(run([pick({ model: A }), reply(1, 422, { ok: false, error: 'x' })]).reads.map((x) => x.delayMs), [0]);
  assert.deepEqual(run([pick({ effort: HIGH }), reply(1, 0, { error: 'timeout' })]).reads.map((x) => x.delayMs), [0]);
  assert.equal(switchNotice(502, { error: 'upstream unreachable' }, 'Sonnet').text,
    'No answer in time. Check the model again in a moment: the switch may still go through.');
});

test('a failure earlier in the chain is forgotten once a later send of the same kind succeeds', () => {
  const r = run([pick({ model: A }), pick({ model: B }), reply(1, 422, { ok: false, error: 'not offered for this agent' }),
    reply(2, 200, ok(200, { switching: 'switched' }))]);
  assert.deepEqual(r.s.notice, { tone: 'ok', text: 'Switched to Haiku.' }, 'no "Couldn\'t switch to Sonnet"');
  const e = run([pick({ effort: HIGH }), pick({ effort: MAX }), reply(1, 409, { ok: false, error: 'busy' }), reply(2, 200, ok(200))]);
  assert.deepEqual(e.s.notice, { tone: 'ok', text: 'Effort set to Max.' });
  // Superseded by a success of the same kind, then another part: the later success still clears it.
  const m = run([pick({ model: A }), pick({ model: B }), pick({ effort: HIGH }), reply(1, 422, { ok: false, error: 'x' }),
    reply(2, 200, ok(200, { switching: 'switched' })), reply(3, 200, ok(200))]);
  assert.deepEqual(m.s.notice, { tone: 'ok', text: 'Switched to Haiku. Effort set to High.' });
  // Another kind's success does not clear it (D still holds).
  const d = run([pick({ model: A }), pick({ effort: HIGH }), reply(1, 422, { ok: false, error: 'x' }), reply(2, 200, ok(200))]);
  assert.equal(d.s.notice?.text, "Couldn't switch to Sonnet: x.");
});

// ── A pick the gateway kept for the turn to end is read again when the turn ends ──

const SINCE = '2026-11-03T12:00:00.123456+00:00';
const AFTER = '2026-11-03T12:01:00+00:00';
const BEFORE = '2026-11-03T11:00:00+00:00';
const P202 = (extra: SwitchBody = {}): SwitchBody => ok(202, { status: 'pending', applies: 'at turn end', pending_since: SINCE, ...extra });
const watches = (out: LiveSwitchEffect[]) => out.filter((e) => e.kind === 'watch').map((e) => (e as { delayMs: number }).delayMs);
const unwatched = (out: LiveSwitchEffect[]) => out.some((e) => e.kind === 'unwatch');
const stillPending: ReadInfo = { model: 'opus', pending: { model: 'sonnet', since: SINCE } };
/** Sonnet picked on a busy agent, 202, the read after it: still waiting. */
const kept = () => run([pick({ model: A }), reply(1, 202, P202()), read(1, stillPending)]);

test('a 202 is awaited and the fallback re-read armed; still pending keeps the pick and re-arms', () => {
  const r = run([pick({ model: A }), reply(1, 202, P202())]);
  assert.deepEqual(r.s.awaiting, { model: A, since: SINCE, polls: TURN_POLL_MAX });
  assert.deepEqual(watches(r.out), [TURN_POLL_MS]);
  const k = kept();
  assert.deepEqual(shownOf(k.s), { model: 'sonnet' });
  assert.deepEqual(watches(k.out).slice(-1), [TURN_POLL_MS]);
  const p = run([{ type: 'poll' }], k.s);
  assert.deepEqual(p.reads, [{ kind: 'resync', delayMs: 0, token: 1, later: false }], 'the watch fired: a read now');
  assert.equal(p.s.awaiting?.polls, TURN_POLL_MAX - 1);
  const still = run([read(1, stillPending)], p.s);
  assert.deepEqual(shownOf(still.s), { model: 'sonnet' });
  assert.deepEqual(still.s.notice, k.s.notice, 'nothing new said');
  assert.deepEqual(watches(still.out), [TURN_POLL_MS]);
  // Not awaiting anything: a stray watch does nothing.
  assert.deepEqual(run([{ type: 'poll' }, { type: 'turnEnd' }]).out, []);
});

test('turn end -> a read after the gateway\'s pass; applied -> "Switched to X." once, the server\'s values show', () => {
  const k = kept();
  const t = run([{ type: 'turnEnd' }], k.s);
  assert.deepEqual(watches(t.out), [TURN_END_READ_MS], 'read once the gateway has had its pass');
  assert.equal(t.reads.length, 0);
  const p = run([{ type: 'poll' }], t.s);
  assert.equal(p.reads.length, 1);
  const applied: ReadInfo = { model: 'sonnet', pending: null, last_switch: { model: 'sonnet', at: AFTER, outcome: 'switched' } };
  const done = run([read(1, applied)], p.s);
  assert.deepEqual(done.s.notice, { tone: 'ok', text: 'Switched to Sonnet.' });
  assert.equal(done.s.awaiting, null);
  assert.deepEqual(shownOf(done.s), {});
  assert.ok(unwatched(done.out));
  const again = run([{ type: 'say', notice: null }, read(1, applied)], done.s);
  assert.equal(again.s.notice, null, 'said once');
  // Applied by the gateway's own record while the seat's record still names the old model: kept, read again later.
  const lag = run([read(1, { model: 'opus', pending: null, last_switch: { model: 'sonnet', at: AFTER, outcome: 'switched' } })], k.s);
  assert.deepEqual(lag.s.notice, { tone: 'ok', text: 'Switched to Sonnet.' });
  assert.deepEqual(shownOf(lag.s), { model: 'sonnet' });
  assert.deepEqual(lag.reads, [{ kind: 'resync', delayMs: RESYNC_AFTER_SWITCH_MS, token: 1, later: true }]);
  // No record of it: the model it is on decides.
  assert.equal(run([read(1, { model: 'sonnet', pending: null })], k.s).s.notice?.text, 'Switched to Sonnet.');
});

test('the gateway closed it failed -> overlay dropped, "Couldn\'t apply X when the turn ended", the pick can be made again', () => {
  const k = kept();
  const failed: ReadInfo = { model: 'opus', pending: null,
    last_switch: { model: 'sonnet', at: AFTER, outcome: 'failed', reason: 'the agent stopped before its turn ended' } };
  const f = run([read(1, failed)], k.s);
  assert.deepEqual(f.s.notice, { tone: 'error', text: "Couldn't apply Sonnet when the turn ended: the agent stopped before its turn ended." });
  assert.deepEqual(shownOf(f.s), {}, 'the picker shows the model it is really on');
  assert.equal(f.s.awaiting, null);
  assert.ok(unwatched(f.out));
  // An older record (before this pick was asked) is not this pick's: without one, nothing is said (review 1).
  const old = run([read(1, { model: 'opus', pending: null, last_switch: { model: 'sonnet', at: BEFORE, outcome: 'failed', reason: 'old' } })], k.s);
  assert.equal(old.s.notice, null, 'the "will apply" words no longer hold: cleared');
  assert.deepEqual(shownOf(old.s), {});
  // Dedupe: while the server still waits on Sonnet, picking it again with an effort out is nothing new...
  assert.equal(run([pick({ effort: HIGH }), pick({ model: A })], k.s).s.waiting, null);
  // ...after the failure it is a new pick again.
  assert.deepEqual(run([pick({ effort: HIGH }), pick({ model: A })], f.s).s.waiting, { model: A });
});

test('an effort kept for the turn end: applied / failed words; a model and an effort said together', () => {
  const e = run([pick({ effort: HIGH }), reply(1, 202, P202({ effort: 'high' }))]);
  assert.deepEqual(e.s.awaiting, { effort: HIGH, since: SINCE, polls: TURN_POLL_MAX });
  const okd = run([read(1, { model: 'opus', effort: 'high', pending: null,
    last_switch: { model: 'opus', effort: 'high', at: AFTER, outcome: 'switched' } })], e.s);
  assert.deepEqual(okd.s.notice, { tone: 'ok', text: 'Effort set to High.' });
  const bad = run([read(1, { model: 'opus', effort: 'low', pending: null,
    last_switch: { model: 'opus', effort: 'high', at: AFTER, outcome: 'failed', reason: 'this seat is headless now' } })], e.s);
  assert.deepEqual(bad.s.notice, { tone: 'error', text: "Couldn't apply High effort when the turn ended: this seat is headless now." });
  assert.deepEqual(shownOf(bad.s), {});
  assert.equal(run([read(1, { model: 'opus', effort: 'high', pending: null })], e.s).s.notice?.text, 'Effort set to High.');
  const gone = run([read(1, { model: 'opus', effort: 'low', pending: null })], e.s);
  assert.equal(gone.s.notice, null, 'no record of a failure: dropped, nothing said, the wait words cleared');
  assert.deepEqual(shownOf(gone.s), {});
  const both = run([pick({ model: A }), pick({ effort: HIGH }), reply(1, 202, P202()), reply(2, 202, P202({ effort: 'high' }))]);
  assert.deepEqual(both.s.awaiting, { model: A, effort: HIGH, since: SINCE, polls: TURN_POLL_MAX });
  const bf = run([read(2, { model: 'opus', effort: null, pending: null,
    last_switch: { model: 'sonnet', effort: 'high', at: AFTER, outcome: 'failed', reason: 'model unavailable.' } })], both.s);
  assert.deepEqual(bf.s.notice, { tone: 'error', text: "Couldn't apply Sonnet and High effort when the turn ended: model unavailable." });
  const bo = run([read(2, { model: 'sonnet', effort: 'high', pending: null,
    last_switch: { model: 'sonnet', effort: 'high', at: AFTER, outcome: 'switched' } })], both.s);
  assert.deepEqual(bo.s.notice, { tone: 'ok', text: 'Switched to Sonnet. Effort set to High.' });
});

test('no verdict on a failed read or a pick replaced elsewhere; a 200 ends the wait; leave/join; busy; the budget', () => {
  const k = kept();
  const nul = run([read(1, null)], k.s);
  assert.deepEqual(shownOf(nul.s), { model: 'sonnet' }, 'a read that failed says nothing');
  assert.ok(nul.s.awaiting);
  assert.deepEqual(watches(nul.out), [TURN_POLL_MS]);
  const other = run([read(1, { model: 'opus', pending: { model: 'haiku', since: AFTER } })], k.s);
  assert.equal(other.s.awaiting, null, 'another surface replaced it');
  assert.deepEqual(shownOf(other.s), {});
  assert.deepEqual(other.s.notice, k.s.notice, 'nothing said about it');
  // The current model picked (the cancel): 200, nothing waits any more.
  const c = run([pick({ model: O }), reply(2, 200, ok(200, { changed: false, cancelled: true, switching: 'switched' }))], k.s);
  assert.equal(c.s.awaiting, null);
  assert.ok(unwatched(c.out));
  // The last picker leaves: the watch stops; one comes back while the engine lives: re-armed.
  const l = run([{ type: 'leave' }], k.s);
  assert.ok(unwatched(l.out));
  assert.deepEqual(watches(run([{ type: 'join' }], l.s).out), [TURN_POLL_MS]);
  // A watch or a turn end while a request is out: no read then; the turn end is owed TURN_END_READ_MS after the drain
  // (the gateway's pass has to run first: review 6).
  const busy = run([pick({ effort: LOW }), { type: 'poll' }, { type: 'turnEnd' }], k.s);
  assert.equal(busy.reads.length, 0);
  const drained = run([reply(2, 202, P202({ effort: 'low' }))], busy.s);
  assert.deepEqual(drained.reads, []);
  assert.deepEqual(watches(drained.out), [TURN_END_READ_MS]);
  assert.equal(drained.s.owed.turn, false);
  assert.deepEqual(drained.s.awaiting, { model: A, effort: LOW, since: SINCE, polls: TURN_POLL_MAX });
  // The fallback stops after TURN_POLL_MAX reads; a turn end still reads.
  let s = k.s;
  for (let i = 0; i < TURN_POLL_MAX; i++) s = run([{ type: 'poll' }, read(1, stillPending)], s).s;
  assert.equal(s.awaiting?.polls, 0);
  assert.deepEqual(watches(run([{ type: 'poll' }, read(1, stillPending)], s).out), [], 'no more fallback reads');
  const te = run([{ type: 'turnEnd' }], s);
  assert.deepEqual(watches(te.out), [TURN_END_READ_MS]);
  assert.equal(run([{ type: 'poll' }], te.s).reads.length, 1);
  assert.equal(TURN_POLL_MS * TURN_POLL_MAX, 15 * 60_000, 'the fallback covers the gateway\'s 15 min stale mark');
});

test('review: never "Couldn\'t apply" without the gateway\'s record of a failure; an unreadable record keeps waiting', () => {
  const k = kept();
  // Cancelled or replaced from another device (a 200 there): nothing waits and nothing was recorded.
  const cancelled = run([read(1, { model: 'opus', pending: null, last_switch: null })], k.s);
  assert.equal(cancelled.s.awaiting, null);
  assert.deepEqual(shownOf(cancelled.s), {}, 'the overlay gives way');
  assert.equal(k.s.notice?.tone, 'wait');
  assert.equal(cancelled.s.notice, null, 'nothing said; "Sonnet will apply when…" cleared');
  // Only a wait notice is cleared: an error (or ok) one stays.
  const errNotice = { tone: 'error' as const, text: "Couldn't set Max effort: busy." };
  const kept2 = run([{ type: 'say', notice: errNotice }, read(1, { model: 'opus', pending: null, last_switch: null })], k.s);
  assert.deepEqual(kept2.s.notice, errNotice);
  // Not every part gone (one applied): the applied words speak.
  const both = run([pick({ model: A }), pick({ effort: HIGH }), reply(1, 202, P202()), reply(2, 202, P202({ effort: 'high' })),
    read(2, { model: 'opus', effort: 'high', pending: null, last_switch: null })]);
  assert.deepEqual(both.s.notice, { tone: 'ok', text: 'Effort set to High.' });
  assert.ok(unwatched(cancelled.out));
  // The record could not be read (pending_error): keep waiting, as for a failed read.
  const unreadable = run([read(1, { model: 'opus', pending: null, last_switch: null, pending_error: 'malformed' })], k.s);
  assert.deepEqual(unreadable.s.awaiting, k.s.awaiting);
  assert.deepEqual(shownOf(unreadable.s), { model: 'sonnet' });
  assert.deepEqual(watches(unreadable.out), [TURN_POLL_MS]);
  // Fail closed on a time that cannot be read: not this pick's record.
  for (const at of ['garbage', undefined]) {
    const r = run([read(1, { model: 'opus', pending: null, last_switch: { model: 'sonnet', at, outcome: 'failed', reason: 'x' } })], k.s);
    assert.equal(r.s.notice, null, `at ${at}`);
  }
  const noSince = run([pick({ model: A }), reply(1, 202, ok(202, { status: 'pending' })),
    read(1, { model: 'opus', pending: null, last_switch: { model: 'sonnet', at: AFTER, outcome: 'failed', reason: 'x' } })]);
  assert.equal(noSince.s.notice, null, 'no since: the record is not matched, so no failure is said (the wait words clear)');
});

test('review: an unseen chain error is kept with the turn-end words; a ruled failure is not kept by a later read owed', () => {
  const k = kept();
  const failed: ReadInfo = { model: 'opus', pending: null,
    last_switch: { model: 'sonnet', at: AFTER, outcome: 'failed', reason: 'model unavailable' } };
  const err = run([{ type: 'say', notice: { tone: 'error', text: "Couldn't set Max effort: busy." } }, read(1, failed)], k.s);
  assert.deepEqual(err.s.notice, { tone: 'error',
    text: "Couldn't set Max effort: busy. Couldn't apply Sonnet when the turn ended: model unavailable." });
  // A switched record's later read still owed (owed.later) does not keep a part just ruled failed.
  const lagging: LiveSwitchState = { ...k.s, owed: { ...k.s.owed, later: true } };
  const f = run([read(1, failed)], lagging);
  assert.deepEqual(shownOf(f.s), {});
  assert.equal(f.s.owed.later, true, 'the later read is still owed');
});

// ── the hook and its per-agent store, run as themselves ──

/**
 * The same no-DOM harness as modelPickerPanel.test.ts (React 19's hook dispatcher, re-rendered until render-phase
 * updates settle), with the hooks useLiveSwitch uses: useState, useRef, useCallback, useEffect (after each render when
 * its deps change, cleanup first) and useSyncExternalStore (subscribed after render, re-subscribed when `subscribe`
 * changes, a change marks it for render). Everything is cleaned up on unmount.
 */
function mountHook<P, R>(hook: (p: P) => R, first: P) {
  const internals = (React as unknown as Record<string, { H: unknown }>).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  const slots: unknown[] = [];
  let at = 0;
  let dirty = false;
  let mounted = true;
  type Eff = { deps?: unknown[]; cleanup?: (() => void) | void; run?: () => (() => void) | void };
  const same = (a?: unknown[], b?: unknown[]) => !!a && !!b && a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
  const effect = (fn: () => (() => void) | void, deps?: unknown[]) => {
    const k = at++;
    const prev = slots[k] as Eff | undefined;
    if (prev && same(prev.deps, deps)) return;
    slots[k] = { deps, cleanup: prev?.cleanup, run: fn } as Eff;
  };
  const dispatcher = {
    useState(init: unknown) {
      const k = at++;
      if (!(k in slots)) slots[k] = typeof init === 'function' ? (init as () => unknown)() : init;
      const set = (v: unknown) => {
        if (!mounted) return;
        const next = typeof v === 'function' ? (v as (p: unknown) => unknown)(slots[k]) : v;
        if (!Object.is(next, slots[k])) { slots[k] = next; dirty = true; }
      };
      return [slots[k], set];
    },
    useRef(v: unknown) { const k = at++; if (!(k in slots)) slots[k] = { current: v }; return slots[k]; },
    useCallback(fn: unknown, deps: unknown[]) {
      const k = at++;
      const prev = slots[k] as { fn: unknown; deps: unknown[] } | undefined;
      if (prev && same(prev.deps, deps)) return prev.fn;
      slots[k] = { fn, deps };
      return fn;
    },
    useEffect: effect,
    useSyncExternalStore(subscribe: (cb: () => void) => () => void, get: () => unknown) {
      effect(() => subscribe(() => { if (mounted) dirty = true; }), [subscribe]);
      return get();
    },
  };
  let props = first;
  let result: R;
  const flushEffects = () => {
    for (const s of slots) {
      const e = s as Eff | undefined;
      if (e && typeof e === 'object' && 'run' in e && e.run) {
        const fn = e.run; e.run = undefined;
        if (typeof e.cleanup === 'function') e.cleanup();
        e.cleanup = fn();
      }
    }
  };
  const render = () => {
    const prev = internals.H;
    let n = 0;
    do {
      internals.H = dispatcher;
      try {
        do { dirty = false; at = 0; result = hook(props); } while (dirty && ++n < 25);
      } finally { internals.H = prev; }
      flushEffects();
    } while (dirty && ++n < 25);
    assert.ok(n < 25, 'render settles');
  };
  render();
  return {
    get current() { return result; },
    rerender(next: Partial<P>) { props = { ...props, ...next }; render(); },
    /** Something outside rendering happened (an answer arrived): render again if it changed, as React would. */
    flush() { if (dirty) render(); },
    unmount() {
      for (const s of slots) { const e = s as Eff | undefined; if (e && typeof e === 'object' && typeof e.cleanup === 'function') e.cleanup(); }
      mounted = false;
    },
  };
}

/** A fake gateway: every PUT waits until the test answers it, in any order. */
function fakeGateway() {
  const calls: { agentId: string; model: string; effort?: string; answer: (status: number, body?: SwitchBody) => void }[] = [];
  const reads: string[] = [];
  const timers: { fn: () => void; ms: number; live: boolean }[] = [];
  const send: LiveSwitchDeps['send'] = (agentId, model, effort) => new Promise((res) => {
    calls.push({ agentId, model, ...(effort ? { effort } : {}),
      answer: (status, body = ok(status)) => res({ status, body }) });
  });
  return {
    calls, reads, timers,
    deps: (agentId: string, baseModel: string | null = 'opus', over: Partial<LiveSwitchDeps> = {}): LiveSwitchDeps => ({
      send, baseModel,
      resync: () => { reads.push(agentId); return Promise.resolve(null); },
      later: (fn, ms) => { const t = { fn, ms, live: true }; timers.push(t); return () => { t.live = false; }; },
      ...over,
    }),
  };
}
const tick = async () => { for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0)); };
type HookProps = { agentId: string; deps: LiveSwitchDeps };
const useIt = ({ agentId, deps }: HookProps) => useLiveSwitch(agentId, deps);
const sent = (gw: ReturnType<typeof fakeGateway>) => gw.calls.map((c) => [c.model, c.effort]);

test('hook: quick picks answered out of order: the agent and the UI end on the newest pick', async () => {
  const gw = fakeGateway();
  const h = mountHook(useIt, { agentId: 'h1', deps: gw.deps('h1') });
  h.current.pick({ model: A }); h.current.pick({ model: B }); h.current.pick({ effort: MAX }); h.flush();
  await tick();
  assert.deepEqual(sent(gw), [['sonnet', undefined]], 'one in flight');
  assert.deepEqual(h.current.shown, { model: 'haiku', effort: 'max' });
  assert.equal(h.current.busy, true);
  gw.calls[0].answer(200, ok(200, { switching: 'switched' })); await tick(); h.flush();
  assert.deepEqual(sent(gw), [['sonnet', undefined], ['haiku', undefined]], 'the model alone');
  gw.calls[1].answer(200, ok(200, { switching: 'switched' })); await tick(); h.flush();
  assert.deepEqual(sent(gw)[2], ['haiku', 'max'], 'then the effort with it');
  gw.calls[2].answer(200, ok(200, { effort: 'max', effort_switching: 'switched' })); await tick(); h.flush();
  assert.deepEqual(h.current.notice, { tone: 'ok', text: 'Switched to Haiku. Effort set to Max.' });
  assert.deepEqual(h.current.shown, { model: 'haiku', effort: 'max' });
  assert.equal(h.current.busy, false);
  assert.deepEqual(gw.timers.filter((t) => t.live).map((t) => t.ms), [RESYNC_AFTER_SWITCH_MS]);
  gw.timers.filter((t) => t.live)[0].fn(); await tick(); h.flush();
  assert.deepEqual(gw.reads, ['h1']);
  assert.deepEqual(h.current.shown, {}, 'then the server\'s own values');
  h.unmount();
});

test('I1: two pickers on one agent share one queue: the panel picks B, the composer (stale read) picks E -> B with E', async () => {
  const gw = fakeGateway();
  const panel = mountHook(useIt, { agentId: 'i1', deps: gw.deps('i1', 'opus') });
  const composer = mountHook(useIt, { agentId: 'i1', deps: gw.deps('i1', 'opus') });
  panel.current.pick({ model: B }); panel.flush(); composer.flush();
  assert.deepEqual(composer.current.shown, { model: 'haiku' }, 'the composer shows the panel\'s pick too');
  assert.deepEqual(composer.current.notice, { tone: 'wait', text: 'Switching to Haiku…' });
  gw.calls[0].answer(200, ok(200, { switching: 'switched' })); await tick();
  composer.current.pick({ effort: HIGH }); composer.flush(); await tick();
  assert.deepEqual(sent(gw), [['haiku', undefined], ['haiku', 'high']], 'never Opus: the composer\'s read was stale');
  // And while the panel's switch is still out, the composer's effort waits behind it.
  const gw2 = fakeGateway();
  const p2 = mountHook(useIt, { agentId: 'i1b', deps: gw2.deps('i1b', 'opus') });
  const c2 = mountHook(useIt, { agentId: 'i1b', deps: gw2.deps('i1b', 'opus') });
  p2.current.pick({ model: B }); c2.current.pick({ effort: HIGH }); await tick();
  assert.equal(gw2.calls.length, 1, 'one in flight for the agent, whichever page picked');
  gw2.calls[0].answer(202, ok(202)); await tick(); p2.flush(); c2.flush();
  assert.deepEqual(sent(gw2), [['haiku', undefined], ['haiku', 'high']]);
  for (const x of [panel, composer, p2, c2]) x.unmount();
});

test('hook: 202 then the read: the pick stays shown while the server still has it waiting', async () => {
  const gw = fakeGateway();
  const info = { model: 'opus', pending: { model: 'sonnet' } };
  const h = mountHook(useIt, { agentId: 'h2', deps: gw.deps('h2', 'opus', { resync: () => Promise.resolve(info) }) });
  h.current.pick({ model: A }); await tick();
  gw.calls[0].answer(202, ok(202, { status: 'pending' })); await tick(); h.flush();
  assert.deepEqual(h.current.notice, { tone: 'wait', text: 'Sonnet will apply when the agent finishes its current turn.' });
  assert.deepEqual(h.current.shown, { model: 'sonnet' });
  h.current.pick({ effort: HIGH }); await tick();
  assert.deepEqual(sent(gw)[1], ['sonnet', 'high'], 'an effort now goes with the waiting model, never cancelling it');
  h.unmount();
});

test('hook: the newest fails after an older one applied: failure shown, real values re-read', async () => {
  const gw = fakeGateway();
  const h = mountHook(useIt, { agentId: 'h3', deps: gw.deps('h3') });
  h.current.pick({ effort: HIGH }); h.current.pick({ effort: MAX }); await tick();
  gw.calls[0].answer(200, ok(200)); await tick();
  gw.calls[1].answer(409, { ok: false, error: 'busy' }); await tick(); h.flush();
  assert.deepEqual(h.current.notice, { tone: 'error', text: "Couldn't set Max effort: busy." });
  assert.deepEqual(gw.reads, ['h3']);
  assert.deepEqual(h.current.shown, {}, 'the server\'s value (High, really applied) shows');
  h.unmount();
});

test('hook: another agent: its own engine; leaving drops the waiting pick; coming back while out shares the answer', async () => {
  const gw = fakeGateway();
  const h = mountHook(useIt, { agentId: 'h4', deps: gw.deps('h4') });
  h.current.pick({ model: A }); h.current.pick({ model: B }); await tick();
  h.rerender({ agentId: 'h4-other', deps: gw.deps('h4-other') });
  assert.equal(h.current.notice, null);
  assert.deepEqual(h.current.shown, {});
  h.rerender({ agentId: 'h4', deps: gw.deps('h4') });
  assert.deepEqual(h.current.shown, { model: 'sonnet' }, 'back while A is out: A shows; B was dropped');
  gw.calls[0].answer(200, ok(200, { switching: 'switched' })); await tick(); h.flush();
  assert.equal(gw.calls.length, 1, 'B never went out');
  assert.deepEqual(h.current.notice, { tone: 'ok', text: 'Switched to Sonnet.' });
  h.unmount();
});

test('hook: an engine with no picker and nothing out is dropped (no leak); a later mount starts clean', async () => {
  const gw = fakeGateway();
  const h = mountHook(useIt, { agentId: 'h5', deps: gw.deps('h5') });
  h.current.pick({ model: A }); await tick();
  h.unmount();
  gw.calls[0].answer(500, { ok: false, error: 'boom' }); await tick();
  assert.deepEqual(gw.reads, [], 'no picker left to re-read for');
  const h2 = mountHook(useIt, { agentId: 'h5', deps: gw.deps('h5') });
  assert.equal(h2.current.notice, null);
  assert.deepEqual(h2.current.shown, {});
  assert.equal(h2.current.busy, false);
  h2.current.pick({ model: B }); await tick();
  assert.equal(gw.calls.length, 2);
  h2.unmount();
  assert.equal(gw.timers.filter((t) => t.live).length, 0);
});

const liveTimers = (gw: ReturnType<typeof fakeGateway>) => gw.timers.filter((t) => t.live).map((t) => t.ms);
/** A timer of `ms` comes due (as the page's clock would fire it). */
function fire(gw: ReturnType<typeof fakeGateway>, ms: number) {
  const t = gw.timers.filter((x) => x.live && x.ms === ms).pop();
  assert.ok(t, `a live ${ms} ms timer`);
  t.live = false;
  t.fn();
}

test('hook: busy -> idle on the page\'s status re-reads after the gateway\'s pass; a failed apply frees the pick', async () => {
  const gw = fakeGateway();
  let info: ReadInfo = stillPending;
  const deps = (status: string) => gw.deps('s3a', 'opus', { status, resync: () => { gw.reads.push('s3a'); return Promise.resolve(info); } });
  const h = mountHook(useIt, { agentId: 's3a', deps: deps('working') });
  h.current.pick({ model: A }); await tick();
  gw.calls[0].answer(202, P202()); await tick(); h.flush();
  assert.deepEqual(h.current.shown, { model: 'sonnet' });
  assert.deepEqual(liveTimers(gw), [TURN_POLL_MS], 'the fallback is armed');
  h.rerender({ deps: deps('working') });
  assert.deepEqual(liveTimers(gw), [TURN_POLL_MS], 'still working: nothing new');
  info = { model: 'opus', pending: null,
    last_switch: { model: 'sonnet', at: AFTER, outcome: 'failed', reason: 'the agent went headless' } };
  h.rerender({ deps: deps('idle') });
  assert.deepEqual(liveTimers(gw), [TURN_END_READ_MS], 'the turn ended: a read after the gateway\'s pass');
  fire(gw, TURN_END_READ_MS); await tick(); h.flush();
  assert.deepEqual(h.current.notice, { tone: 'error', text: "Couldn't apply Sonnet when the turn ended: the agent went headless." });
  assert.deepEqual(h.current.shown, {});
  assert.deepEqual(liveTimers(gw), []);
  // Sonnet again is a new pick: it goes out.
  h.current.pick({ model: A }); await tick();
  assert.deepEqual(sent(gw), [['sonnet', undefined], ['sonnet', undefined]]);
  h.unmount();
});

test('hook: no turn end seen: the fallback reads every 20 s until it applied; "Switched to X." once, timers stop', async () => {
  const gw = fakeGateway();
  let info: ReadInfo = stillPending;
  const h = mountHook(useIt, { agentId: 's3b', deps: gw.deps('s3b', 'opus', { resync: () => { gw.reads.push('s3b'); return Promise.resolve(info); } }) });
  h.current.pick({ model: A }); await tick();
  gw.calls[0].answer(202, P202()); await tick(); h.flush();
  assert.deepEqual(gw.reads, ['s3b'], 'the 202 read now');
  fire(gw, TURN_POLL_MS); await tick(); h.flush();
  assert.deepEqual(gw.reads, ['s3b', 's3b']);
  assert.deepEqual(h.current.shown, { model: 'sonnet' }, 'still waiting');
  assert.deepEqual(liveTimers(gw), [TURN_POLL_MS]);
  info = { model: 'sonnet', pending: null, last_switch: { model: 'sonnet', at: AFTER, outcome: 'switched' } };
  fire(gw, TURN_POLL_MS); await tick(); h.flush();
  assert.deepEqual(h.current.notice, { tone: 'ok', text: 'Switched to Sonnet.' });
  assert.deepEqual(h.current.shown, {});
  assert.deepEqual(liveTimers(gw), []);
  h.unmount();
});

test('hook: unmount stops the watch, also with a request still out; a picker coming back re-arms it', async () => {
  const gw = fakeGateway();
  const d = () => gw.deps('s3c', 'opus', { resync: () => Promise.resolve(stillPending) });
  const h = mountHook(useIt, { agentId: 's3c', deps: d() });
  h.current.pick({ model: A }); await tick();
  gw.calls[0].answer(202, P202()); await tick(); h.flush();
  assert.deepEqual(liveTimers(gw), [TURN_POLL_MS]);
  h.unmount();
  assert.deepEqual(liveTimers(gw), [], 'no picker: no timer');
  const h2 = mountHook(useIt, { agentId: 's3c', deps: d() });
  h2.current.pick({ model: A }); await tick();
  gw.calls[1].answer(202, P202()); await tick(); h2.flush();
  h2.current.pick({ effort: HIGH }); await tick();
  const back = mountHook(useIt, { agentId: 's3c', deps: d() });
  h2.unmount(); back.unmount();
  assert.deepEqual(liveTimers(gw), [], 'every picker gone, the effort still out: no timer');
  const again = mountHook(useIt, { agentId: 's3c', deps: d() });
  assert.deepEqual(liveTimers(gw), [TURN_POLL_MS], 'the engine lives (a request is out) and still awaits Sonnet: re-armed');
  again.unmount();
  gw.calls[2].answer(202, P202({ effort: 'high' })); await tick();
  assert.deepEqual(liveTimers(gw), []);
});

test('review: a turn-end read is not pushed back by a later fallback watch', async () => {
  const gw = fakeGateway();
  const deps = (status: string) => gw.deps('s3d', 'opus', { status, resync: () => Promise.resolve(stillPending) });
  const h = mountHook(useIt, { agentId: 's3d', deps: deps('working') });
  h.current.pick({ model: A }); await tick();
  gw.calls[0].answer(202, P202()); await tick(); h.flush();
  h.rerender({ deps: deps('idle') });
  assert.deepEqual(liveTimers(gw), [TURN_END_READ_MS]);
  h.current.pick({ effort: HIGH }); await tick();
  gw.calls[1].answer(202, P202({ effort: 'high' })); await tick(); h.flush();
  assert.deepEqual(liveTimers(gw), [TURN_END_READ_MS], 'the drain\'s 20 s watch keeps the earlier deadline');
  fire(gw, TURN_END_READ_MS); await tick(); h.flush();
  assert.deepEqual(liveTimers(gw), [TURN_POLL_MS], 'still pending: the fallback from then on');
  h.unmount();
});

test('wiring: every live picker goes through the sequencer; Settings defaults do not', () => {
  const src = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8');
  const composer = src('../components/agent/Composer.tsx');
  assert.match(composer, /useLiveSwitch\(agentId,/);
  assert.match(composer, /^\s*status,$/m, 'the composer gives the sequencer the agent\'s status (its turn end)');
  assert.match(src('../pages/AgentPage.tsx'), /<Composer[\s\S]*?status=\{seatStatus\}/);
  assert.doesNotMatch(composer, /switchAgentModel\(/, 'no direct switch: every pick is sequenced');
  assert.doesNotMatch(composer, /effortAfter/);
  const panel = src('../components/arturo/ArturoPanel.tsx');
  assert.match(panel, /useLiveSwitch\(agentId,/);
  assert.doesNotMatch(panel, /switchAgentModel\(agentId, id\)/);
  assert.doesNotMatch(src('./agentModel.ts'), /export function effortAfter/, 'the guessed revert is gone');
  assert.doesNotMatch(src('../components/settings/sections/AgentsSection.tsx'), /useLiveSwitch/);
});
