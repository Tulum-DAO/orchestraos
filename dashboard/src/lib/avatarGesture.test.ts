// The thinking gestures (components/avatar/avatarGesture.ts), checked against the reference
// (the measured reference): deterministic clips, the picker, the entrance, the sphere, and the model's
// working state (no breathing, chaining, reduced motion).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as G from '../components/avatar/avatarGesture.ts';
import * as C from '../components/avatar/avatarCore.ts';

test('every clip is deterministic and ends where its last pose says', () => {
  for (const k of G.GESTURE_KEYS) {
    const keys = G.GESTURES[k], d = G.clipDur(keys);
    for (const t of [0, 0.05, d / 3, d / 2, d - 0.01]) assert.deepEqual(G.gestureFrame(keys, t, G.NEUTRAL), G.gestureFrame(keys, t, G.NEUTRAL));
    const end = G.gestureFrame(keys, d + 1, G.NEUTRAL), last = keys[keys.length - 1];
    assert.equal(end.ew, G.EYES[last.eye][0], k);
    assert.ok(d > 0.5 && d < 9, `${k}: ${d.toFixed(2)} s`);
  }
});

test('a clip starts from the pose the last one ended on (chaining)', () => {
  const from = G.settle(G.gestureFrame(G.GESTURES.recede, 99, G.NEUTRAL));
  assert.deepEqual(G.gestureFrame(G.GESTURES.squint, 0, from), from);
});

test('Roll somersaults and Spin turns all the way round, then settle back to facing front', () => {
  const roll = G.GESTURES.roll, spin = G.GESTURES.spin;
  const r = G.gestureFrame(roll, G.clipDur(roll), G.NEUTRAL), sp = G.gestureFrame(spin, G.clipDur(spin), G.NEUTRAL);
  assert.ok(Math.abs(r.pitch + 2 * Math.PI) < 1e-9);
  assert.ok(Math.abs(sp.yaw + 2 * Math.PI) < 1e-9);
  assert.ok(Math.abs(G.settle(r).pitch) < 1e-9 && Math.abs(G.settle(sp).yaw) < 1e-9);
  // halfway through the somersault the eyes are round the back
  const rollStart = G.clipDur(roll.slice(0, -1)), before = G.gestureFrame(roll.slice(0, -1), 99, G.NEUTRAL);
  const last = roll[roll.length - 1], somersault = G.moveTiming(last, before, G.gestureFrame([last], 99, before));
  const mid = G.gestureFrame(roll, rollStart + 0.5 * somersault, G.NEUTRAL);
  assert.equal(G.eyeOnSphere(mid, -1), null);
});

test('eye moves start slow, peak early and land long; the holds keep their full length', () => {
  const one: G.GestureKey[] = [{ yaw: 0.3, eye: 'capsule', hold: 1 }];
  const mv = G.moveTiming(one[0], G.NEUTRAL, G.gestureFrame(one, 9, G.NEUTRAL));
  assert.ok(Math.abs(mv - (G.moveDur(one[0]) + G.TURN.land)) < 1e-9);
  assert.ok(Math.abs(G.clipDur(one) - (mv + 1)) < 1e-9);
  const at = (t: number) => G.gestureFrame(one, t, G.NEUTRAL).yaw, dt = 1e-4, v = (t: number) => (at(t + dt) - at(t)) / dt;
  let peakT = 0; for (let t = 0; t < mv; t += mv / 200) if (v(t) > v(peakT)) peakT = t;
  assert.ok(peakT / mv > 0.3 && peakT / mv < 0.45, `peak at ${(peakT / mv).toFixed(2)} of the move`);
  assert.ok(v(0.05 * mv) < 0.15 * v(peakT) && v(0.9 * mv) < 0.15 * v(peakT), 'slow start and slow landing');
  assert.equal(at(mv), 0.3);
  for (const k of G.GESTURE_KEYS) {
    const keys = G.GESTURES[k], d = G.clipDur(keys);
    assert.deepEqual(G.gestureFrame(keys, d, G.NEUTRAL), G.gestureFrame(keys, d + 5, G.NEUTRAL), k);
  }
});

test('a far look turns no faster than TURN.speed; full turns keep their speed', () => {
  const sq = G.GESTURES.squint, prev = G.gestureFrame(sq.slice(0, 2), 99, G.NEUTRAL), to = G.gestureFrame(sq.slice(0, 3), 99, G.NEUTRAL);
  const mv = G.moveTiming(sq[2], prev, to);
  const k = 3 / G.TURN.speed; // 0.5-0.7 s at 3 rad/s
  assert.ok(mv > 0.5 * k && mv < 0.7 * k, `far look takes ${mv.toFixed(2)} s`);
  const start = G.moveTiming(sq[0], G.NEUTRAL, G.NEUTRAL) + sq[0].hold + G.moveTiming(sq[1], G.NEUTRAL, prev) + sq[1].hold;
  let peak = 0; for (let t = 0; t < mv; t += 0.002) peak = Math.max(peak, Math.abs(G.gestureFrame(sq, start + t + 0.002, G.NEUTRAL).yaw - G.gestureFrame(sq, start + t, G.NEUTRAL).yaw) / 0.002);
  assert.ok(peak <= G.TURN.speed * 1.02, `peak ${peak.toFixed(2)} rad/s`);
  assert.ok(G.moveTiming(G.GESTURES.spin[0], G.NEUTRAL, G.gestureFrame(G.GESTURES.spin, 99, G.NEUTRAL)) < 1);
});

test('pauses wander: about half the long holds drift a little after a beat, then rest; continuous into the next move', () => {
  const keys: G.GestureKey[] = [{ eye: 'capsule', hold: 2 }, { yaw: 0.4, eye: 'capsule', hold: 2 }];
  let wandered = 0, n = 0;
  for (let seed = 1; seed < 400; seed++) {
    const mv = G.moveTiming(keys[0], G.NEUTRAL, G.NEUTRAL);
    const a = G.gestureFrame(keys, mv + 0.1, G.NEUTRAL, seed), b = G.gestureFrame(keys, mv + 1.99, G.NEUTRAL, seed);
    assert.equal(a.yaw, 0, 'still for the first beat');
    n++;
    if (b.yaw !== 0) {
      wandered++;
      assert.ok(Math.abs(b.yaw) >= 0.079 && Math.abs(b.yaw) <= 0.181 && Math.abs(b.pitch) <= 0.1001);
      // the next move starts where the wander ended (no jump)
      const e = G.gestureFrame(keys, mv + 2, G.NEUTRAL, seed), e2 = G.gestureFrame(keys, mv + 2.001, G.NEUTRAL, seed);
      assert.ok(Math.abs(e.yaw - b.yaw) < 1e-3 && Math.abs(e2.yaw - e.yaw) < 1e-3);
    }
    assert.deepEqual(G.gestureFrame(keys, 1.5, G.NEUTRAL, seed), G.gestureFrame(keys, 1.5, G.NEUTRAL, seed));
  }
  assert.ok(wandered / n > 0.4 && wandered / n < 0.6, `wandered ${wandered}/${n}`);
  // no seed, short holds and blinks never wander
  assert.equal(G.gestureFrame(keys, 1.9, G.NEUTRAL).yaw, 0);
  const blink = G.GESTURES.roll.find(k => k.ease === 'blink')!;
  for (let seed = 1; seed < 50; seed++) assert.equal(G.gestureFrame([blink], 0.1, G.NEUTRAL, seed).yaw, blink.yaw);
});

test('blinks keep their quick timing (not slowed)', () => {
  const blinks = G.GESTURES.roll.filter((k) => k.ease === 'blink');
  assert.equal(blinks.length, 2);
  for (const b of blinks) assert.equal(G.moveDur(b), 0.06);
});

test('the picker: no immediate repeats, recent ones damped (no A-B-A loops), Roll most common, reading varies its version', () => {
  let s = 7; const rand = () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
  let hist: G.GestureName[] = [];
  const n: Record<string, number> = {}, v: Record<string, number> = {};
  let aba = 0, sameRead = 0, lastRead: string | null = null;
  const N = 40000;
  for (let i = 0; i < N; i++) {
    const k = G.pickGesture(rand, hist), pk = G.pickOf(k);
    if (hist.length) assert.notEqual(pk, G.pickOf(hist[hist.length - 1]));
    if (hist.length >= 2 && pk === G.pickOf(hist[hist.length - 2])) aba++;
    if (G.READING.includes(k)) { if (k === lastRead) sameRead++; lastRead = k; v[k] = (v[k] || 0) + 1; }
    n[pk] = (n[pk] || 0) + 1; hist = G.remember(hist, k);
  }
  assert.equal(Object.keys(n).length, G.PICK_KEYS.length);
  assert.equal(Object.entries(n).sort((a, b) => b[1] - a[1])[0][0], 'roll');
  // without the memory, A-B-A runs ~15% of the time; with it, far rarer
  assert.ok(aba / N < 0.06, `A-B-A ${(aba / N).toFixed(3)}`);
  assert.equal(sameRead, 0, 'reading never plays the same version twice running');
  for (const r of G.READING) assert.ok(Math.abs(v[r] / n.reading - 0.25) < 0.03, `${r}: ${(v[r] / n.reading).toFixed(3)}`);
  // the old single-name form still works
  assert.notEqual(G.pickGesture(rand, 'roll'), 'roll');
});

test('each time an avatar appears the gesture order is different; a fixed gestureSeed repeats it', () => {
  const order = (opts: { gestureSeed?: number }) => {
    const m = new C.AvatarModel({ agentId: 'same-agent', state: 'working', ...opts }), out: string[] = [];
    for (let i = 0; i < 60 * 40; i++) { m.step(1 / 60, null); const nm = m.gesture?.name; if (nm && nm !== 'hub' && out[out.length - 1] !== nm) out.push(nm); }
    return out.join(' ');
  };
  assert.notEqual(order({}), order({}));
  assert.equal(order({ gestureSeed: 42 }), order({ gestureSeed: 42 }));
});

test('the entrance: nothing, then a faint 0.2 dot at 33 ms, full and opaque by 167 ms, never past full', () => {
  assert.deepEqual(G.entranceFrame(0.01), { scale: 0, alpha: 0 });
  const a = G.entranceFrame(0.034);
  assert.ok(a.scale > 0.19 && a.scale < 0.25 && a.alpha < 0.3);
  assert.deepEqual(G.entranceFrame(0.167), { scale: 1, alpha: 1 });
  let prev = 0;
  for (let t = 0.033; t <= 0.3; t += 0.004) { const f = G.entranceFrame(t); assert.ok(f.scale >= prev && f.scale <= 1); prev = f.scale; }
});

test('eyes on the sphere: front-facing neutral at +-0.16 A, narrowing apart as the head turns, hidden round the back', () => {
  const l = G.eyeOnSphere(G.NEUTRAL, -1)!, r = G.eyeOnSphere(G.NEUTRAL, 1)!;
  assert.ok(Math.abs(l.x + 0.16) < 1e-9 && Math.abs(r.x - 0.16) < 1e-9 && Math.abs(l.y) < 1e-9);
  const t = { ...G.NEUTRAL, yaw: 0.8 }, tl = G.eyeOnSphere(t, -1)!, tr = G.eyeOnSphere(t, 1)!;
  const sep = tr.x - tl.x;
  assert.ok(sep > 0.18 && sep < 0.3, `turned separation ${sep.toFixed(3)}`);
  assert.ok(Math.abs(tr.m[0]) < Math.abs(r.m[0]), 'foreshortened toward the rim');
  assert.equal(G.eyeOnSphere({ ...G.NEUTRAL, yaw: Math.PI }, 1), null);
});

test('working: the body is dead still between gestures (no breathing, no bob) and the head keeps gesturing', () => {
  const m = new C.AvatarModel({ agentId: 'w', state: 'working' });
  const eyes = new Set<string>();
  for (let i = 0; i < 60 * 20; i++) {
    const p = m.step(1 / 60, null);
    assert.equal(p.sx, 1); assert.equal(p.sy, 1); assert.equal(p.dy, 0); assert.equal(p.lid, 1);
    assert.ok(p.gesture);
    eyes.add(p.gesture.ew.toFixed(3));
  }
  assert.ok(eyes.size >= 4, `eye shapes seen in 20 s: ${eyes.size}`);
});

test('working has no entrance pose: the first frame is the neutral capsules, already in the first picked clip', () => {
  const m = new C.AvatarModel({ agentId: 'e', state: 'working' });
  assert.deepEqual(m.step(1 / 60, null).gesture, { ...G.NEUTRAL });
  assert.notEqual(m.gesture!.name, 'hub');
});

test('Roll opens up-left, then up-right, then the middle, before the flip', () => {
  const k = G.GESTURES.roll;
  assert.ok(k[0].yaw! < 0 && k[0].pitch! < 0);
  const right = k.findIndex(x => (x.yaw ?? 0) > 0), mid = k.findIndex((x, i) => i > right && !x.yaw && x.pitch! < 0 && x.pitch! > -1);
  const flip = k.findIndex(x => (x.pitch ?? 0) <= -Math.PI);
  assert.ok(right > 0 && mid > right && flip > mid, `${right} ${mid} ${flip}`);
});

test('leaving working drops the gesture; other states draw the usual face', () => {
  const m = new C.AvatarModel({ agentId: 'x', state: 'working' });
  for (let i = 0; i < 30; i++) m.step(1 / 60, null);
  m.setState('idle');
  assert.equal(m.step(1 / 60, null).gesture, null);
  m.setState('thinking');
  assert.equal(m.step(1 / 60, null).gesture, null);
});

test('back from typing, the gestures wait for the face to reform, then start from neutral', () => {
  const m = new C.AvatarModel({ agentId: 'y', state: 'thinking' });
  for (let i = 0; i < 60; i++) m.step(1 / 60, null);
  m.setState('working');
  assert.equal(m.step(1 / 60, null).gesture, null);
  for (let i = 0; i < 60; i++) m.step(1 / 60, null);
  const p = m.step(1 / 60, null);
  assert.ok(p.gesture);
});

test('reduced motion and still frames: working holds the neutral capsules', () => {
  const m = new C.AvatarModel({ agentId: 'r', state: 'working', reducedMotion: true });
  assert.deepEqual(m.step(1 / 60, null).gesture, G.NEUTRAL);
  const s = new C.AvatarModel({ agentId: 's', state: 'working' });
  assert.deepEqual(s.stillPose().gesture, G.NEUTRAL);
});

test('which roles gesture: the row and the drawer always, the sidebar only when selected', () => {
  assert.equal(C.gesturesAllowed({ role: 'activity' }), true);
  assert.equal(C.gesturesAllowed({ role: 'drawer' }), true);
  assert.equal(C.gesturesAllowed({ role: 'sidebar', selected: false }), false);
  assert.equal(C.gesturesAllowed({ role: 'sidebar', selected: true }), true);
});

test('the neutral capsules sit inside every body shape (on its face sphere)', () => {
  for (const k of C.SHAPE_KEYS) {
    const s = C.SHAPES[k], { cy, r } = G.faceSphere(s), A = 2 * r, pts = C.shapeOutline(k);
    const inside = (x: number, y: number) => { let c = false; for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) { const [xi, yi] = pts[i], [xj, yj] = pts[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; };
    for (const sg of [-1, 1]) {
      const e = G.eyeOnSphere(G.NEUTRAL, sg)!, w = G.NEUTRAL.ew * A * e.m[0], h = G.NEUTRAL.eh * A;
      for (const [dx, dy] of [[-w / 2, 0], [w / 2, 0], [0, -h / 2], [0, h / 2]]) assert.ok(inside(e.x * A + dx, cy + e.y * A + dy), `${k} eye ${sg}`);
    }
  }
});

test('Recede: the body shrinks to 0.73 A and springs back past full size to ~1.03 A before settling', () => {
  const keys = G.GESTURES.recede, d = G.clipDur(keys);
  let lo = 9, hi = 0;
  for (let t = 0; t <= d; t += 1 / 240) { const s = G.gestureFrame(keys, t, G.NEUTRAL).s; lo = Math.min(lo, s); hi = Math.max(hi, s); }
  assert.ok(Math.abs(lo - 0.73) < 1e-9);
  assert.ok(hi > 1.025 && hi < 1.035, `peak ${hi.toFixed(3)}`);
  assert.equal(G.gestureFrame(keys, d, G.NEUTRAL).s, 1);
});

test('reading: tilted halfway down at a fixed height; each cycle a slow glide left to right and a quick return; 2-3 cycles', () => {
  // halfway between rest and fully down: rest y = 0, fully down = the bottom of the sphere
  const rest = G.eyeOnSphere(G.NEUTRAL, -1)!.y, tilted = G.eyeOnSphere({ ...G.NEUTRAL, lat: G.READ_LAT }, -1)!.y;
  const fullDown = G.eyeOnSphere({ ...G.NEUTRAL, lat: Math.PI / 2 - 1e-3 }, -1)?.y ?? Math.cos(Math.asin(2 * G.EYE_X)) / 2;
  assert.ok(Math.abs(tilted - (rest + fullDown) / 2) < 1e-3, `tilted ${tilted.toFixed(4)} vs halfway ${((rest + fullDown) / 2).toFixed(4)}`);
  for (const [name, cycles] of [['read', 3], ['skim', 2], ['readBig', 3], ['readSmall', 2]] as const) {
    const keys = G.GESTURES[name], scanKey = keys[1], T = G.moveDur(scanKey), t0 = G.clipDur(keys.slice(0, 1));
    assert.equal(T, scanKey.move);
    assert.ok(cycles >= 2 && cycles <= 3 && scanKey.scan!.cycles === cycles);
    const dt = 1 / 600, at = (t: number) => G.gestureFrame(keys, t0 + t, G.NEUTRAL);
    const y0 = G.eyeOnSphere(at(0), -1)!.y;
    let rightward = 0, leftward = 0, maxJump = 0, turns = 0, prevDir = 0;
    for (let t = 0; t < T; t += dt) {
      const a = at(t), b = at(t + dt), dy = b.yaw - a.yaw;
      // the height never changes (both eyes)
      for (const sg of [-1, 1]) assert.ok(Math.abs(G.eyeOnSphere(a, sg)!.y - y0) < 1e-9, `${name}: height drifted`);
      if (dy > 1e-9) rightward += dt; else if (dy < -1e-9) leftward += dt;
      const dir = Math.sign(dy); if (dir && prevDir && dir !== prevDir) turns++; if (dir) prevDir = dir;
      maxJump = Math.max(maxJump, Math.abs(dy));
    }
    assert.ok(rightward > 2.5 * leftward, `${name}: glide ${rightward.toFixed(2)} s vs return ${leftward.toFixed(2)} s`);
    assert.equal(turns, 2 * cycles - 1, `${name}: ${turns} turns`);
    assert.ok(maxJump < 0.02, `${name}: no jumps`);
  }
});

test('round-eyed reading: big circles and small circles, really round', () => {
  for (const [name, eye] of [['readBig', 'bigRound'], ['readSmall', 'smallRound']] as const) {
    const k = G.GESTURES[name][1];
    assert.equal(k.eye, eye);
    assert.equal(G.EYES[eye][0], G.EYES[eye][1]);
  }
  assert.ok(G.EYES.bigRound[0] > G.EYES.capsule[0] && G.EYES.smallRound[0] < G.EYES.dot[0]);
  // the big circles never touch each other at the neutral spacing
  assert.ok(2 * G.EYE_X - G.EYES.bigRound[0] > 0.04);
});

test('activity from the live step: thinking, and tools mapped to reading / editing / running / searching', () => {
  assert.equal(G.activityFor({ kind: 'thinking' }), 'thinking');
  for (const [tool, a] of [['Read', 'reading'], ['Grep', 'reading'], ['Glob', 'reading'], ['WebFetch', 'reading'], ['Edit', 'editing'],
    ['MultiEdit', 'editing'], ['Write', 'editing'], ['Bash', 'running'], ['Task', 'running'], ['WebSearch', 'searching']] as const) {
    assert.equal(G.activityFor({ kind: 'tool', tool }), a, tool);
  }
  assert.equal(G.activityFor({ kind: 'tool', tool: 'SomethingElse' }), 'none');
  assert.equal(G.activityFor(null, 'Bash'), 'running');
  assert.equal(G.activityFor({ kind: 'idle' }), 'none');
  assert.equal(G.activityFor(undefined), 'none');
});

test('the activity tilts the mix (weights, not rules): reading reads more, thinking ponders more, running looks around more', () => {
  const share = (activity: G.Activity) => {
    let s = 11; const rand = () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
    const hist: G.GestureName[] = [], n: Record<string, number> = {}, v: Record<string, number> = {};
    for (let i = 0; i < 30000; i++) { const k = G.pickGesture(rand, hist.slice(-8), activity); n[G.pickOf(k)] = (n[G.pickOf(k)] || 0) + 1; v[k] = (v[k] || 0) + 1; hist.push(k); }
    const tot = Object.values(n).reduce((a, b) => a + b, 0);
    return { p: (k: string) => (n[k] || 0) / tot, v, n };
  };
  const base = share('none'), rd = share('reading'), th = share('thinking'), ru = share('running'), ed = share('editing'), se = share('searching');
  assert.ok(rd.p('reading') > 2 * base.p('reading'), `reading ${rd.p('reading').toFixed(3)} vs ${base.p('reading').toFixed(3)}`);
  // (the recent-gesture damping evens things out a little, so a x2.5 weight shows as ~x1.5-1.7 in the mix)
  assert.ok(th.p('ponder') > 1.4 * base.p('ponder') && th.p('reading') < 0.5 * base.p('reading'), `ponder ${th.p('ponder').toFixed(3)} vs ${base.p('ponder').toFixed(3)}`);
  assert.ok(ru.p('lookAround') > 1.4 * base.p('lookAround'), `lookAround ${ru.p('lookAround').toFixed(3)} vs ${base.p('lookAround').toFixed(3)}`);
  assert.ok(ed.p('squint') > 1.4 * base.p('squint'), `squint ${ed.p('squint').toFixed(3)} vs ${base.p('squint').toFixed(3)}`);
  // every gesture can still come up under every activity
  for (const s of [rd, th, ru, ed, se]) assert.equal(Object.keys(s.n).length, G.PICK_KEYS.length);
  // editing reads closely (Read), searching skims (Skim)
  assert.ok(ed.v.read > 1.5 * (ed.v.readBig ?? 0) && se.v.skim > 1.5 * (se.v.readSmall ?? 0));
});

test('a step change re-picks at the next still moment for the new activity, with no jump', () => {
  const m = new C.AvatarModel({ agentId: 'act', state: 'working', gestureSeed: 5 });
  for (let i = 0; i < 60 * 3; i++) m.step(1 / 60, null);
  // force a long Ponder hold, then switch the activity mid-hold
  const from = m.step(1 / 60, null).gesture!;
  m.gesture = { name: 'ponder', keys: G.GESTURES.ponder, t0: m.t, dur: G.clipDur(G.GESTURES.ponder, from), from };
  for (let i = 0; i < 60; i++) m.step(1 / 60, null);   // into the hold
  m.setActivity('reading');
  let prev = m.step(1 / 60, null).gesture!, switched = -1;
  for (let i = 0; i < 60 * 2; i++) {
    const g = m.step(1 / 60, null).gesture!;
    if (switched < 0 && m.gesture?.name !== 'ponder') {
      switched = i;
      // the cut happens while the face is still, and the next gesture starts from exactly there
      assert.ok(Math.abs(g.yaw - prev.yaw) < 0.02 && Math.abs(g.pitch - prev.pitch) < 0.02 && Math.abs(g.roll - prev.roll) < 0.02, 'no jump at the switch');
    }
    prev = g;
  }
  assert.ok(switched >= 0 && switched < 10, `re-picked after ${switched} frames`);
  assert.equal(m.activity, 'reading');
  // an unknown activity is 'none'
  m.setActivity('juggling'); assert.equal(m.activity, 'none');
});
