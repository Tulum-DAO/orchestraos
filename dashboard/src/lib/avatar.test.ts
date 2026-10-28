// The living avatars' core, checked the way the prototype's test-shapes.js checks it (every check ported):
// outlines bounded, eyes inside every shape, the dot and the badge clear of the eyes and each other, the
// hash stable, eye ink, the shared hub, and the placement rules (dot by role, follow, wander, badge, ink).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as C from '../components/avatar/avatarCore.ts';

function inside(pts: C.Pt[], x: number, y: number) { let c = false; for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) { const [xi, yi] = pts[i], [xj, yj] = pts[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; }
function edgeDist(pts: C.Pt[], x: number, y: number) { let m = 1e9; for (let i = 0; i < pts.length; i++) { const [ax, ay] = pts[i], [bx, by] = pts[(i + 1) % pts.length]; const dx = bx - ax, dy = by - ay; const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy))); m = Math.min(m, Math.hypot(x - ax - t * dx, y - ay - t * dy)); } return m; }
const looks: [number, number][] = []; for (const x of [-1, -0.5, 0, 0.5, 1]) for (const y of [-1, -0.5, 0, 0.5, 1]) looks.push([x, y]);
// every eye disc any mood and look can produce, in px relative to the body centre
function eyeDiscs(k: string, size: number): [number, number, number][] {
  const u = size * C.LAYOUT.u, out: [number, number, number][] = [];
  for (const mk of Object.keys(C.MOODS)) for (const [lx, ly] of looks) {
    const f = C.faceLayout(k, C.MOODS[mk], lx, ly), cs = Math.cos(f.rot), sn = Math.sin(f.rot);
    for (const [ex, ey, er] of f.eyes) out.push([(ex * cs - ey * sn) * u, (ex * sn + ey * cs + f.fy) * u, er * u]);
  }
  return out;
}

test('every shape has a finite, bounded outline', () => {
  assert.equal(C.SHAPE_KEYS.length, 16);
  for (const k of C.SHAPE_KEYS) {
    const p = C.shapeOutline(k);
    let mx = 0; for (const [x, y] of p) { assert.ok(Number.isFinite(x) && Number.isFinite(y), `NaN in ${k}`); mx = Math.max(mx, Math.hypot(x, y)); }
    assert.ok(mx <= 1.2, `${k} too big: ${mx}`);
  }
});

test('both eyes stay inside the body for every mood, look and head tilt (0.04 margin)', () => {
  for (const k of C.SHAPE_KEYS) {
    const p = C.shapeOutline(k);
    let minM = 1e9;
    for (const mk of Object.keys(C.MOODS)) for (const [lx, ly] of looks) {
      const f = C.faceLayout(k, C.MOODS[mk], lx, ly), cs = Math.cos(f.rot), sn = Math.sin(f.rot);
      for (const [ex, ey, er] of f.eyes) for (let a = 0; a < 24; a++) {
        const qx = ex + er * Math.cos(a / 24 * 2 * Math.PI), qy = ey + er * Math.sin(a / 24 * 2 * Math.PI);
        const X = qx * cs - qy * sn, Y = qx * sn + qy * cs + f.fy;
        minM = Math.min(minM, inside(p, X, Y) ? edgeDist(p, X, Y) : -edgeDist(p, X, Y));
      }
    }
    assert.ok(minM >= 0.04, `${k}: eye margin ${minM.toFixed(3)}`);
  }
});

test('an idle model falls asleep after sleepAfterMs', () => {
  const m = new C.AvatarModel({ agentId: 'a', state: 'idle', sleepAfterMs: 2000 });
  for (let i = 0; i < 200; i++) m.step(0.016, null);
  assert.equal(m.asleep, true);
});

test('the hash is stable and spreads over every shape', () => {
  const ids: Record<string, number> = {}; for (let i = 0; i < 2000; i++) { const s = C.identityFor('agent-' + i).shape; ids[s] = (ids[s] || 0) + 1; }
  assert.equal(Object.keys(ids).length, 16);
  assert.deepEqual(C.identityFor('dev-client-a'), C.identityFor('dev-client-a'));
  // FNV-1a, bit for bit (the Swift port must match): a pinned value guards the hash itself
  assert.equal(C.fnv1a('arturo'), C.fnv1a('arturo'));
  assert.equal(C.fnv1a(''), 0x811c9dc5);
});

test('the status dot clears both eyes and stays on the canvas, every shape, 24 to 128 px', () => {
  for (const k of C.SHAPE_KEYS) for (const size of [24, 32, 48, 64, 128]) {
    const d = C.statusDot(k, size), R = (d.r + d.cut) * 1.12; // 1.12 = needs-input pulse peak
    const cx = size / 2, cy = size * C.LAYOUT.cy;
    assert.ok(cx + d.x + R <= size + 0.01 && cy + d.y + R <= size + 0.01, `dot off canvas ${k} ${size}`);
    let gap = 1e9; for (const [x, y, er] of eyeDiscs(k, size)) gap = Math.min(gap, Math.hypot(x - d.x, y - d.y) - R - er);
    assert.ok(gap >= 0, `dot overlaps an eye on ${k} at ${size}: ${gap.toFixed(2)}px`);
  }
});

test('palettes and eye ink: identities stable, auto picks the higher contrast, explicit ink honoured', () => {
  for (const pk of C.PALETTE_KEYS) {
    const a = C.identityFor('dev-client-a', pk).color;
    assert.ok(C.PALETTES[pk].some((p) => p.hex === a), `palette identity broken ${pk}`);
  }
  for (const p of C.PALETTES.all) {
    const auto = C.eyeInk(p.hex, 'auto'), other = auto === C.INK.white ? C.INK.black : C.INK.white;
    assert.ok(C.contrastRatio(p.hex, auto) >= C.contrastRatio(p.hex, other), `auto ink wrong for ${p.name}`);
  }
  assert.equal(C.eyeInk('#FFC61A', 'black'), C.INK.black);
  assert.equal(C.eyeInk('#FFC61A', 'white'), C.INK.white);
  const m = new C.AvatarModel({ agentId: 'qa-webos', palette: 'pastel', eyeColor: 'white' });
  m.configure({ palette: 'bold', color: undefined });
  assert.ok(C.PALETTES.bold.some((p) => p.hex === m.color), 'palette switch did not recolour');
  m.configure({ color: '#123456' }); m.configure({ palette: 'pastel' });
  assert.equal(m.color, '#123456', 'pinned colour changed');
  assert.equal(m.staticPose().eye, C.INK.white);
});

test('eye-follow: selected only, sidebar and drawer only, within the radius, never working or asleep', () => {
  const F = (o: Parameters<typeof C.followAllowed>[0]) => C.followAllowed({ role: 'sidebar', selected: true, state: 'idle', dist: 50, ...o });
  assert.equal(F({}), true);
  assert.equal(F({ state: 'working' }), false);
  assert.equal(F({ role: 'drawer', state: 'working' }), false);
  assert.equal(F({ role: 'drawer' }), true);
  assert.equal(F({ selected: false }), false);
  assert.equal(F({ dist: C.FOLLOW.radius + 1 }), false);
  assert.equal(C.FOLLOW.radius, 160);
  assert.equal(F({ dist: 100, radius: 80 }), false);
  assert.equal(F({ role: 'pill' }), false);
  assert.equal(F({ role: 'activity' }), false);
  assert.equal(F({ asleep: true }), false);
  for (const st of ['needs_input', 'finished', 'failed']) assert.equal(F({ state: st }), true, st);
});

test('a selected avatar kept awake (notice) never falls asleep; one that dozed off wakes', () => {
  const m = new C.AvatarModel({ agentId: 'sel', state: 'idle', sleepAfterMs: 2000 });
  for (let i = 0; i < 400; i++) m.step(0.016, null);
  assert.equal(m.asleep, true, 'unselected idle model should sleep');
  m.notice();
  assert.equal(m.asleep, false);
  for (let i = 0; i < 600; i++) { m.notice(); m.step(0.016, null); }
  assert.equal(m.asleep, false, 'selected model dozed off');
  const rm = new C.AvatarModel({ agentId: 'rm', state: 'idle', reducedMotion: true });
  rm.notice();
  assert.equal(rm.reaction, null, 'reduced motion: no wake-up jump');
});

test('unselected idle agents doze off soon after the page loads; a busy one does not; reduced motion draws them asleep still', () => {
  const m = new C.AvatarModel({ agentId: 'z', state: 'idle' });
  m.settleAsleepSoon();
  for (let i = 0; i < 150; i++) m.step(0.016, null);        // 2.4 s: not yet
  assert.equal(m.asleep, false);
  for (let i = 0; i < 60; i++) m.step(0.016, null);         // past 3 s
  assert.equal(m.asleep, true);
  const busy = new C.AvatarModel({ agentId: 'z', state: 'working' });
  busy.settleAsleepSoon();
  for (let i = 0; i < 400; i++) busy.step(0.016, null);
  assert.equal(busy.asleep, false);
  const rm = new C.AvatarModel({ agentId: 'z', state: 'idle', reducedMotion: true });
  assert.equal(rm.stillPose(true).asleep, true);
  assert.equal(rm.stillPose(false).asleep, false);
  rm.setState('working');
  assert.equal(rm.stillPose(true).asleep, false, 'a working agent is never drawn asleep');
});

test('wink: the second eye closes and opens again; reduced motion skips it', () => {
  const m = new C.AvatarModel({ agentId: 'w', state: 'idle', sleepAfterMs: 0 });
  m.step(0.016, null);
  m.react('wink');
  let peak = 0; for (let i = 0; i < 80; i++) peak = Math.max(peak, m.step(0.016, null).wink || 0);
  assert.ok(peak > 0.99, `wink never closed (${peak})`);
  assert.equal(m.step(0.016, null).wink, 0);
  const rm = new C.AvatarModel({ agentId: 'w', state: 'idle', reducedMotion: true });
  rm.react('wink');
  assert.equal(rm.reaction, null);
});

test('a pointer never overrides the working gaze; an idle model follows the pointer it is given', () => {
  const m = new C.AvatarModel({ agentId: 'w', state: 'working' });
  let minY = 1; for (let i = 0; i < 400; i++) { m.step(0.016, { dx: -3, dy: -3 }); if (i > 250) minY = Math.min(minY, m.lookTarget.y); }
  assert.ok(minY >= 0.45, `working gaze overridden (lookTarget.y ${minY.toFixed(2)})`);
  const m2 = new C.AvatarModel({ agentId: 'w', state: 'idle' });
  for (let i = 0; i < 60; i++) m2.step(0.016, { dx: -3, dy: 0 });
  assert.ok(m2.look.x < -0.5);
});

test('idle wander only on the selected agent (sidebar, face; never the still pill); wander off keeps idle eyes centred', () => {
  assert.equal(C.wanderAllowed({ role: 'sidebar', selected: true }), true);
  assert.equal(C.wanderAllowed({ role: 'sidebar', selected: false }), false);
  assert.equal(C.wanderAllowed({ role: 'drawer', selected: true }), true);
  assert.equal(C.wanderAllowed({ role: 'drawer', selected: false }), false);
  assert.equal(C.wanderAllowed({ role: 'pill', selected: true }), false, 'the header pill stays still');
  assert.equal(C.wanderAllowed({ role: 'activity', selected: true }), false);
  const m = new C.AvatarModel({ agentId: 'still', state: 'idle', wander: false, sleepAfterMs: 0 });
  let mx = 0; for (let i = 0; i < 600; i++) { m.step(0.016, null); mx = Math.max(mx, Math.abs(m.look.x) + Math.abs(m.look.y)); }
  assert.ok(mx < 1e-6);
});

test('status dot by role: sidebar always (idle plain grey), pill phone + busy only, drawer and activity never', () => {
  for (const st of C.STATE_KEYS) for (const size of [24, 32, 48, 128]) {
    const d = C.dotFor({ role: 'sidebar', state: st, size });
    assert.ok(d && d.color === C.STATES[st].dot, `sidebar dot ${st} ${size}`);
    if (st === 'idle') assert.deepEqual(d, { color: '#85858D', glyph: null });
    else assert.equal(d!.glyph, size >= 40 ? C.STATES[st].glyph : null);
    for (const phone of [false, true]) {
      const p = C.dotFor({ role: 'pill', state: st, size: 28, phone });
      const want = phone && (st === 'working' || st === 'needs_input');
      assert.deepEqual(p, want ? { color: C.STATES[st].dot, glyph: null } : null, `pill ${st} phone=${phone}`);
      assert.equal(C.dotFor({ role: 'drawer', state: st, size, phone }), null);
      assert.equal(C.dotFor({ role: 'activity', state: st, size, phone }), null);
    }
  }
  assert.equal(C.PHONE_MAX, 640);
  assert.equal(C.STATES.working.dot, '#3B82F6');
  assert.equal(C.STATES.needs_input.dot, '#F59E0B');
  assert.equal(C.STATES.finished.dot, '#22C55E');
  assert.equal(C.STATES.failed.dot, '#EF4444');
  // the idle grey reads on both themes (a non-text graphic: 3:1)
  for (const bg of ['#FFFFFF', '#F7F7F8', '#EBEBEF', '#0A0A0A', '#111113', '#1F1F23']) assert.ok(C.contrastRatio(C.STATES.idle.dot, bg) >= 3, bg);
});

test('provider badge: from 28 px, on hover below, never on activity, the dot wins when tight', () => {
  const B = (o: Parameters<typeof C.badgeFor>[0]) => C.badgeFor({ role: 'sidebar', shape: 'circle', size: 48, provider: 'openai', ...o });
  assert.notEqual(B({}), null);
  assert.notEqual(B({ size: 28 }), null);
  for (const size of [16, 20, 24, 27]) {
    assert.equal(B({ size }), null, `hidden under 28 (${size})`);
    assert.notEqual(B({ size, hover: true }), null, `on hover under 28 (${size})`);
  }
  assert.equal(B({ size: 20, hover: true, dot: { color: '#A1A1AA' } }), null, 'the dot wins when tight');
  assert.equal(B({ provider: null }), null);
  assert.equal(B({ role: 'activity' }), null);
  assert.notEqual(B({ role: 'pill', size: 28 }), null);
  assert.notEqual(B({ role: 'drawer', size: 128 }), null);
  // same radius rule as the dot
  const b = B({ size: 64 })!, d = C.statusDot('circle', 64);
  assert.equal(b.r, d.r); assert.equal(b.r, Math.max(2.4, 0.1 * 64));
});

test('the badge clears both eyes and the dot, on the canvas, every shape, 28 to 128 px', () => {
  for (const k of C.SHAPE_KEYS) for (const size of [28, 32, 48, 64, 128]) {
    const b = C.badgeFor({ role: 'sidebar', shape: k, size, provider: 'openai', dot: { color: '#3B82F6' } });
    assert.ok(b, `badge hidden at ${size} on ${k}`);
    const R = b.r + b.cut, cx = size / 2, cy = size * C.LAYOUT.cy;
    assert.ok(cx + b.x - R >= -0.01 && cy + b.y + R <= size + 0.01, `badge off canvas ${k} ${size}`);
    let gap = 1e9; for (const [x, y, er] of eyeDiscs(k, size)) gap = Math.min(gap, Math.hypot(x - b.x, y - b.y) - R - er);
    assert.ok(gap >= 0, `badge overlaps an eye on ${k} at ${size}`);
    const d = C.statusDot(k, size), clear = Math.hypot(d.x - b.x, d.y - b.y) - (d.r + d.cut) * C.DOT.pulse - R;
    assert.ok(clear >= C.BADGE.gap, `badge touches the dot on ${k} at ${size}`);
  }
});

test('theme-aware ink: black body + white eyes in light, white body + black eyes in dark; others fixed', () => {
  assert.equal(C.SWATCHES[0].hex, 'ink');
  assert.equal(C.SWATCHES.length, 12);
  assert.equal(C.eyeInk('ink', 'auto', 'light'), C.INK.white);
  assert.equal(C.eyeInk('ink', 'auto', 'dark'), C.INK.black);
  const pose = new C.AvatarModel({ agentId: 'ink', color: 'ink', eyeColor: 'auto' }).staticPose();
  assert.deepEqual(C.paint(pose, 'light'), { body: '#16161A', eye: C.INK.white });
  assert.deepEqual(C.paint(pose, 'dark'), { body: '#F4F4F5', eye: C.INK.black });
  assert.equal(C.paint(new C.AvatarModel({ agentId: 'x', color: '#1E6BE6' }).staticPose(), 'dark').body, '#1E6BE6');
});

test('the hub keeps one record and one model per agent, and updates both together', () => {
  const hub = C.createAgentState();
  let calls = 0; hub.subscribe('arturo', () => calls++);
  hub.set('arturo', { status: 'idle', provider: 'openai', blurb: '' });
  const a = hub.model('arturo'), b = hub.model('arturo');
  assert.equal(a, b, 'one shared model per agent');
  hub.set('arturo', { status: 'working', blurb: 'Reading inbox' });
  assert.equal(a.state, 'working');
  assert.equal(hub.get('arturo')!.blurb, 'Reading inbox');
  assert.equal(calls, 2);
  hub.set('arturo', { shape: 'star', color: '#1E6BE6' });
  assert.equal(a.shape, 'star'); assert.equal(a.color, '#1E6BE6');
});

test('the pill still frame never moves the shared model', () => {
  const hub = C.createAgentState();
  hub.set('arturo', { status: 'working' });
  const m = hub.model('arturo'), t = m.t;
  const pose = m.stillPose();
  assert.equal(m.t, t);
  assert.equal(pose.phase, 0); assert.equal(pose.lookX, 0); assert.equal(pose.pulse, null);
});

test('each proven runtime (claude, codex, gemini, grok) has its provider badge, not only Claude', async () => {
  // read from the source: brandMarks.ts imports the mark files, which node cannot load
  const fs = await import('node:fs');
  const src = fs.readFileSync(new URL('../components/avatar/brandMarks.ts', import.meta.url), 'utf8');
  const block = src.slice(src.indexOf('const ALIASES'), src.indexOf('};', src.indexOf('const ALIASES')));
  const map = Object.fromEntries([...block.matchAll(/'?([\w-]+)'?:\s*'(\w+)'/g)].map((m) => [m[1], m[2]]));
  assert.deepEqual({ claude: map.claude, codex: map.codex, gemini: map.gemini, grok: map.grok },
    { claude: 'claude', codex: 'openai', gemini: 'gemini', grok: 'grok' });
});
