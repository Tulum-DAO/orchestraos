import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextPhase, avatarStateForCall, type CallPhase, type CallEvent } from './callPhase.ts';
import { RollingTimeline, barLook, levelOf, SLICE_MS, SLOTS, TAPER_SLOTS, MIN_H, MAX_H } from './callWave.ts';
import { AvatarModel, STATES, MELT, meltFrame } from '../components/avatar/avatarCore.ts';

const run = (events: CallEvent[], from: CallPhase = 'connecting') => events.reduce((p, e) => nextPhase(p, e), from);

test('a call: connecting, listening, thinking the moment you stop, talking while the agent speaks, ended', () => {
  assert.equal(run([{ type: 'session', state: 'live' }]), 'listening');
  assert.equal(run([{ type: 'session', state: 'live' }, { type: 'userSpeaking' }, { type: 'userDone' }]), 'thinking');
  assert.equal(run([{ type: 'session', state: 'live' }, { type: 'userDone' }, { type: 'playback', playing: true }]), 'talking');
  assert.equal(run([{ type: 'session', state: 'live' }, { type: 'playback', playing: true }, { type: 'playback', playing: false }]), 'listening');
  assert.equal(run([{ type: 'session', state: 'live' }, { type: 'playback', playing: true }, { type: 'userSpeaking' }]), 'talking', 'a barge-in: its voice plays until it stops');
  assert.equal(run([{ type: 'session', state: 'live' }, { type: 'session', state: 'idle' }]), 'ended');
  assert.equal(nextPhase('ended', { type: 'playback', playing: true }), 'ended');
  assert.deepEqual(['listening', 'thinking', 'talking', 'connecting'].map((p) => avatarStateForCall(p as CallPhase)), ['listening', 'thinking', 'talking', 'listening']);
});

test('the waveform\'s timeline: new slices enter on the right and scroll left, one bar per slice', () => {
  const tl = new RollingTimeline();
  tl.push(0, 0.8, 0);
  assert.equal(tl.push(SLICE_MS - 1, 0.2, 0), false, 'the slice is still open');
  assert.equal(tl.push(SLICE_MS, 0, 0), true);
  assert.deepEqual(tl.slots[SLOTS - 1], { user: 0.8, agent: 0 }, 'the loudest of the slice, at the right');
  tl.push(SLICE_MS * 2, 0, 0.6);
  tl.push(SLICE_MS * 3, 0, 0);
  assert.deepEqual(tl.slots[SLOTS - 3], { user: 0.8, agent: 0 }, 'two slices later it has moved two bars left');
  assert.deepEqual(tl.slots[SLOTS - 1], { user: 0, agent: 0.6 }, 'the agent\'s voice in the same row');
  assert.equal(tl.slice, 3);
});

test('bars taper to dots at the left edge; silence is a dotted baseline that flows', () => {
  const loudRight = barLook(SLOTS - 1, 1, 0), loudLeft = barLook(0, 1, 0), loudMid = barLook(TAPER_SLOTS / 2, 1, 0);
  assert.equal(loudRight.h, MAX_H);
  assert.equal(loudLeft.h, MIN_H, 'the oldest bar is a dot');
  assert.ok(loudMid.h > MIN_H && loudMid.h < MAX_H);
  assert.ok(loudLeft.o < loudMid.o && loudMid.o < loudRight.o, 'and fades toward the left');
  const a = barLook(20, 0, 0), b = barLook(20, 0, 1);
  assert.equal(a.h, MIN_H); assert.equal(b.h, MIN_H);
  assert.notEqual(a.o, b.o, 'the dots alternate by slice: the baseline moves');
  assert.equal(barLook(21, 0, 0).o, b.o, 'one slice later each dot looks like its right neighbour did');
  // loudness from bytes: 128 is silence
  assert.equal(levelOf(new Uint8Array(64).fill(128)), 0);
  assert.ok(levelOf(Uint8Array.from({ length: 64 }, (_, i) => (i % 2 ? 200 : 56))) > 0.8);
});

test('thinking: the body itself becomes the middle dot (the one melt), then reforms when it talks (every look)', () => {
  for (const shape of ['pebble', 'hexagon', 'star']) {
    const m = new AvatarModel({ agentId: 'sample', shape, state: 'listening' });
    assert.equal(m.step(1 / 60, null).meltT, 0);
    m.setState('thinking');
    let pose = m.step(1 / 60, null);
    for (let i = 0; i < 60; i++) pose = m.step(1 / 60, null);    // one second
    assert.equal(pose.melt, 1, `${shape}: melted`);
    assert.ok(pose.meltT > MELT.dur, 'the clock runs on for the wave');
    const f = meltFrame(pose.meltT);
    assert.equal(f.shrink, 1); assert.equal(f.eyes, 0); assert.equal(f.side, MELT.gap);
    m.setState('talking');
    for (let i = 0; i < 12; i++) pose = m.step(1 / 60, null);   // the same change, backwards, from where it is
    assert.ok(pose.meltT > 0 && pose.meltT < MELT.dur, `${shape}: reforming`);
    for (let i = 0; i < 30; i++) pose = m.step(1 / 60, null);
    assert.equal(pose.meltT, 0, `${shape}: reformed`);
  }
  assert.ok(STATES.thinking.melt && !STATES.talking.melt && !STATES.listening.melt);
  // reduced motion: a still frame per state, melted for thinking, the dots equal and still
  const still = new AvatarModel({ agentId: 'sample', state: 'thinking', reducedMotion: true }).step(1 / 60, null);
  assert.equal(still.melt, 1);
  assert.deepEqual(meltFrame(still.meltT, true).lit, [1, 1, 1]);
});
