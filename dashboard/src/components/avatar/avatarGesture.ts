/*
 * Thinking gestures: what the face does while an agent works, matched to the reference
 * (a 58 s reference clip at 60 fps, every frame measured).
 *
 * The reference body is dead still between gestures. The life is in the head: the eyes sit on the surface of a
 * sphere, and a gesture turns the head (yaw, pitch, roll) so the eyes slide across it, foreshorten toward the rim
 * and can roll off it and come back from the other side; the eyes also change shape. Gestures are authored clips,
 * each a fixed list of poses, picked at random (weighted, never the same one twice running) with the neutral
 * capsules as the usual hub between them. The status phrase does not drive them.
 *
 * Pure numbers and tables, no DOM (avatarCore's rules), so the Swift port can follow it line for line.
 * Sizes are shares of the body's diameter A; angles are radians. yaw > 0 turns the eyes right, pitch > 0 down,
 * roll > 0 clockwise.
 */

/** One frame of the head: where the eyes are and what shape, plus the body's own scale and shift (shares of A). */
export interface GesturePose {
  yaw: number; pitch: number; roll: number; ew: number; eh: number; s: number; dx: number; dy: number;
  /** A downward tilt applied BEFORE the yaw (a latitude): the eyes then turn along one level line, so their height
   *  stays fixed while they move left and right (reading). 0 / absent for every other gesture. */
  lat?: number;
}

/** Eye shapes, each eye's width x height as shares of A (the reference's "The eyes"). */
export const EYES = {
  capsule: [0.20, 0.40],   // neutral: at rest, attentive
  narrow:  [0.125, 0.28],  // focused, squinting
  tilt:    [0.25, 0.38],   // with a head roll of 20-35 degrees: wondering up / pondering down
  dot:     [0.17, 0.18],   // looking far off
  oval:    [0.25, 0.23],   // a wide-eyed look-around
  dash:    [0.20, 0.05],   // a blink
  slant:   [0.16, 0.25],   // small slanted eyes (Roll's opening look up-left)
  bigRound:   [0.26, 0.26], // reading with big round eyes
  smallRound: [0.13, 0.13], // reading with small round eyes
} as const;
export type EyeShape = keyof typeof EYES;
/** Where the neutral eyes sit: centres at x = +-EYE_X (shares of A), level with the centre. */
export const EYE_X = 0.16;

/** One pose in a clip: reached over `move` seconds (stretched by GLIDE, see moveDur), then held for `hold`.
 *  Eases: 'glide' (the default) starts slow, peaks mid-move and slows before landing; 'back' glides the eyes but
 *  springs the body's scale past its target (the spring-back); 'blink' and 'snap' keep their own quick timing. */
export interface GestureKey {
  yaw?: number; pitch?: number; roll?: number; lat?: number; eye: EyeShape; s?: number; dx?: number; dy?: number;
  move?: number; hold: number; ease?: 'glide' | 'back' | 'blink' | 'snap' | 'scan';
  /** ease 'scan' (reading): `cycles` times, the eyes glide slowly from left to right (`yaw` rad each side of centre)
   *  and come back quickly from right to left; `back` is the return's share of each cycle. */
  scan?: { cycles: number; yaw: number; back: number };
}
export type GestureName = 'roll' | 'squint' | 'recede' | 'lookAround' | 'ponder' | 'spin' | 'peek' | 'read' | 'skim' | 'readBig' | 'readSmall';

/** Moves: median 0.13 s in the reference (90% under 0.32 s), as authored in the clips. */
export const MOVE = 0.13;
/** A little slower, starting slow and slowing again before landing. Every eye move (not blinks,
 *  not snaps) runs GLIDE times its authored length on a quintic ease (no jolt at either end); its peak
 *  speed mid-move is 1.875 / (1.5 x GLIDE) = ~93% of the reference's smoothstep at GLIDE 1.35 (between
 *  1.5 with TURN { speed: 3, land: 0.3 }, too slow, and 1.2 with TURN { speed: 4, land: 0.15 }, too fast). */
export const GLIDE = 1.35;
/** Eases that keep their authored timing: blinks, snaps and reading's scan. */
const exact = (k: GestureKey) => k.ease === 'blink' || k.ease === 'snap' || k.ease === 'scan';
export function moveDur(k: GestureKey): number {
  const mv = k.move ?? MOVE;
  return exact(k) ? mv : mv * GLIDE;
}
const UP_RIGHT = { yaw: 0.35, pitch: -0.41, roll: 0.45 };   // tilted capsules, gaze (+0.17, -0.20) A
const DOWN_LEFT = { yaw: -0.14, pitch: 0.35, roll: -0.45 }; // tilted capsules, gaze (-0.07, +0.17) A

/** Reading's tilt: the eyes halfway between their rest height and fully down (the bottom of the sphere), i.e. a
 *  latitude of asin(0.5) = 30 degrees. */
export const READ_LAT = Math.asin(0.5);
function reading(cycles: number, eye: EyeShape, A: number, glideS: number, backS: number): GestureKey[] {
  const back = backS / (glideS + backS);
  return [
    { lat: READ_LAT, yaw: -A, eye, hold: 0.15 },     // tilt down to the start of the line
    { lat: READ_LAT, yaw: -A, eye, move: cycles * (glideS + backS), ease: 'scan', scan: { cycles, yaw: A, back }, hold: 0.15 },
    { eye: 'capsule', hold: 0.4 },
  ];
}

/** The clips, pose for pose from the reference's "The gesture library" (plus reading). */
export const GESTURES: Record<GestureName, GestureKey[]> = {
  // turning it over: small slanted eyes up-left, capsules up-left, tilted up-right (with a blink), back to the middle,
  // the body dips with the eyes low, then the eyes roll up over the top, round the back and in from the bottom
  // (a backward flip, ~0.35 s), and settle. The reference opens on this clip (0.1-3 s), so its first look is up-left.
  roll: [
    { yaw: -0.35, pitch: -0.35, roll: -0.4, eye: 'slant', hold: 0.4 },
    { yaw: -0.3, pitch: -0.3, eye: 'capsule', hold: 0.3 },
    { ...UP_RIGHT, eye: 'tilt', hold: 0.5 },
    { ...UP_RIGHT, eye: 'dash', move: 0.06, ease: 'blink', hold: 0.05 },
    { ...UP_RIGHT, eye: 'tilt', move: 0.06, ease: 'blink', hold: 0.4 },
    { pitch: -0.15, eye: 'capsule', hold: 0.3 },
    { pitch: 0.4, eye: 'capsule', dy: 0.10, hold: 0.5 },
    { pitch: -2 * Math.PI, eye: 'capsule', move: 0.35, hold: 1.2 },
  ],
  // focused, checking something to the side
  squint: [
    { eye: 'capsule', hold: 0.6 },
    { eye: 'narrow', hold: 0.8 },
    { yaw: -0.9, eye: 'dot', dx: -0.10, hold: 1.1 },
    { eye: 'narrow', hold: 0.9 },
  ],
  // stepping back to think, then an idea: shrink to 0.73 A with tiny dots up-left, 0.88 A looking right, then spring
  // to full size (overshooting to ~1.03 A) with tilted capsules up-right
  recede: [
    { eye: 'dot', hold: 0.5 },
    { yaw: -0.4, pitch: -0.4, eye: 'dot', s: 0.73, move: 0.5, hold: 1.1 },
    { yaw: 0.4, eye: 'dot', s: 0.88, move: 0.2, hold: 0.7 },
    { ...UP_RIGHT, eye: 'tilt', s: 1, move: 0.3, ease: 'back', hold: 0.8 },
  ],
  // scanning: wide ovals left, down-left, up-left, then centre
  lookAround: [
    { yaw: -0.5, eye: 'oval', hold: 0.9 },
    { yaw: -0.45, pitch: 0.35, eye: 'oval', hold: 0.9 },
    { yaw: -0.45, pitch: -0.35, eye: 'oval', hold: 0.9 },
    { eye: 'oval', hold: 2.5 },
    { eye: 'capsule', hold: 0.8 },
  ],
  // pondering: tilted down-left, held
  ponder: [{ ...DOWN_LEFT, eye: 'tilt', hold: 5 }],
  // a flourish: the eyes slide off the left rim and come back from the right (a full turn)
  spin: [{ yaw: -2 * Math.PI, eye: 'capsule', move: 0.4, hold: 0.3 }],
  // glancing up: narrow, eyes at the upper-right rim, narrow left, tilted up
  peek: [
    { eye: 'narrow', hold: 0.8 },
    { yaw: 0.8, pitch: -0.7, eye: 'capsule', move: 0.2, hold: 1.2 },
    { yaw: -0.35, eye: 'narrow', move: 0.2, hold: 1.0 },
    { ...UP_RIGHT, eye: 'tilt', hold: 0.8 },
  ],
  // reading: tilted down to READ_LAT, the eyes' height fixed for the whole motion; each cycle
  // they glide slowly left to right and come back quickly right to left. Read: three slow cycles, eyes narrowed.
  // Skim: two quicker cycles, eyes open.
  read: reading(3, 'narrow', 0.4, 1.5, 0.4),
  skim: reading(2, 'capsule', 0.45, 1.1, 0.35),
  // the same reading with round eyes: big circles, three cycles; small circles, two
  readBig: reading(3, 'bigRound', 0.4, 1.5, 0.4),
  readSmall: reading(2, 'smallRound', 0.4, 1.3, 0.35),
};
export const GESTURE_KEYS = Object.keys(GESTURES) as GestureName[];

/** Reading is ONE gesture in the rotation; when it is picked, one of its four versions plays, each
 *  equally likely. */
export const READING: GestureName[] = ['read', 'skim', 'readBig', 'readSmall'];
/** What the picker chooses between: every clip on its own, except the reading versions, which share one entry. */
export type PickName = Exclude<GestureName, 'read' | 'skim' | 'readBig' | 'readSmall'> | 'reading';
/** How often each comes up: Roll most (about 4 a minute in the reference), the others 1-2. */
export const WEIGHTS: Record<PickName, number> = { roll: 3, squint: 1.5, recede: 1.5, lookAround: 1.5, peek: 1.2, ponder: 1, spin: 0.8, reading: 1.2 };
export const PICK_KEYS = Object.keys(WEIGHTS) as PickName[];
export const pickOf = (k: GestureName): PickName => (READING.includes(k) ? 'reading' : k as PickName);

/** What the agent is doing right now (its live step), so the gestures fit it. */
export type Activity = 'reading' | 'thinking' | 'editing' | 'running' | 'searching' | 'none';
export const ACTIVITIES: Activity[] = ['reading', 'thinking', 'editing', 'running', 'searching', 'none'];
const TOOL_ACTIVITY: [RegExp, Activity][] = [
  [/^(read|grep|glob|ls|view|cat|webfetch|notebookread|fetch)/i, 'reading'],
  [/^(edit|multiedit|write|notebookedit|apply_?patch|str_replace)/i, 'editing'],
  [/^(websearch|search)/i, 'searching'],
  [/^(bash|shell|exec|run|task|agent|test|monitor)/i, 'running'],
];
/** The activity for a live step (lib/liveStep AgentStep: kind tool | thinking | waiting | idle, and the tool's name). */
export function activityFor(step?: { kind?: string; tool?: string } | null, tool?: string): Activity {
  if (step?.kind === 'thinking') return 'thinking';
  const name = (step?.kind === 'tool' ? step.tool : undefined) ?? tool;
  if (!name) return 'none';
  for (const [re, a] of TOOL_ACTIVITY) if (re.test(name)) return a;
  return 'none';
}
/** How the activity tilts the odds (weight multipliers; missing = 1). Weights, not rules: anything can still come up.
 *  Thinking leans on looking away (gaze aversion reads as thinking: RESEARCH.md #9). */
export const ACTIVITY: Record<Activity, Partial<Record<PickName, number>>> = {
  reading:   { reading: 4, squint: 1.5, roll: 0.5, spin: 0.3 },
  thinking:  { ponder: 2.5, recede: 1.8, peek: 1.6, roll: 1.3, reading: 0.3 },
  editing:   { squint: 2.5, reading: 1.5, lookAround: 0.4, spin: 0.3 },
  running:   { lookAround: 2.5, peek: 2, reading: 0.3, ponder: 0.6 },
  searching: { lookAround: 2.5, reading: 2, ponder: 0.4 },
  none:      {},
};
/** Which reading version an activity prefers (missing = 1): editing reads closely, searching skims. */
export const READING_BIAS: Partial<Record<Activity, Partial<Record<GestureName, number>>>> = {
  editing: { read: 3 },
  searching: { skim: 3 },
};
/** Pauses between gestures, scaled by activity: waiting on a command is calmer, with longer drifting pauses. */
export const ACTIVITY_HUB: Record<Activity, number> = { reading: 1, thinking: 1.2, editing: 1, running: 1.5, searching: 1, none: 1 };

/** Between clips: back to the neutral capsules for HUB.min..max seconds, except that a clip may chain straight into
 *  the next one (the reference does at 11-16 s) with probability HUB.chain. */
export const HUB = { min: 0.4, max: 1.2, chain: 0.3 };


export const NEUTRAL: GesturePose = { yaw: 0, pitch: 0, roll: 0, ew: EYES.capsule[0], eh: EYES.capsule[1], s: 1, dx: 0, dy: 0 };
export function hubClip(hold: number): GestureKey[] { return [{ eye: 'capsule', hold }]; }

function keyPose(k: GestureKey): GesturePose {
  const [ew, eh] = EYES[k.eye];
  const p: GesturePose = { yaw: k.yaw ?? 0, pitch: k.pitch ?? 0, roll: k.roll ?? 0, ew, eh, s: k.s ?? 1, dx: k.dx ?? 0, dy: k.dy ?? 0 };
  if (k.lat) p.lat = k.lat;
  return p;
}
const glide = (k: number) => k * k * k * (k * (6 * k - 15) + 10);
const ease = {
  glide,
  blink: (k: number) => k * k * (3 - 2 * k),
  // reading's scan: the base pose glides; the swing is added in gestureFrame
  scan: glide,
  // easeOutBack, c = 3: peaks 25% of the move past the target, so the 0.88 -> 1 spring-back reaches 1.03 A
  back: (k: number) => { const c = 3, x = k - 1; return 1 + (c + 1) * x * x * x + c * x * x; },
  snap: (k: number) => k,
};
const mix = (a: GesturePose, b: GesturePose, k: number): GesturePose => {
  const p: GesturePose = {
    yaw: a.yaw + (b.yaw - a.yaw) * k, pitch: a.pitch + (b.pitch - a.pitch) * k, roll: a.roll + (b.roll - a.roll) * k,
    ew: a.ew + (b.ew - a.ew) * k, eh: a.eh + (b.eh - a.eh) * k, s: a.s + (b.s - a.s) * k, dx: a.dx + (b.dx - a.dx) * k, dy: a.dy + (b.dy - a.dy) * k,
  };
  const la = a.lat ?? 0, lb = b.lat ?? 0;
  const lat = la + (lb - la) * k;
  if (lat) p.lat = lat;   // lat 0 is the same as none: keep the pose's keys canonical
  return p;
};


/** The head at `t` seconds into a clip that starts from pose `from` (the last clip's end, so clips chain). Pure. */
/** A far look that whips over and freezes feels unnatural. So an eye move (glide or back) is one
 *  curve that starts slow, peaks early and lands long (`land`: glide of 1-(1-p)^1.3, ~39% speeding up, ~61% settling
 *  in), and a big turn takes longer: the head never turns faster than TURN.speed rad/s at its peak. The holds keep
 *  their full length (the clip runs longer instead). Full turns (Roll, Spin: over PI) keep their speed; blinks and
 *  the entrance snap are untouched. */
export const TURN = { speed: 3.5, land: 0.25 };
const land = (k: number) => glide(1 - Math.pow(1 - k, 1.3));
const LAND_PEAK = (() => { let m = 0; for (let i = 0; i < 1000; i++) m = Math.max(m, (land((i + 1) / 1000) - land(i / 1000)) * 1000); return m; })();
/** How long the move into `k` takes from `prev` (seconds). */
export function moveTiming(k: GestureKey, prev: GesturePose, to: GesturePose): number {
  const base = moveDur(k);
  if (exact(k)) return base;
  const d = Math.max(Math.abs(to.yaw - prev.yaw), Math.abs(to.pitch - prev.pitch), Math.abs(to.roll - prev.roll));
  return Math.max(base + TURN.land, d < Math.PI ? LAND_PEAK * d / TURN.speed : 0);
}

/** The eyes wander a bit on some pauses. A hold of at least WANDER.minHold s, with chance
 *  WANDER.chance, drifts after a beat (WANDER.delay of the hold, at least 0.2 s) to a nearby spot (yaw 0.08-0.18 rad,
 *  toward the centre when already turned; pitch up to +-0.1) over up to WANDER.dur s on the landing curve, and rests
 *  there; the next move starts from where the eyes ended up. Blinks and snaps never wander. Picked from the clip's
 *  seed, so a clip with the same seed plays the same; no seed = no wander. */
export const WANDER = { chance: 0.5, minHold: 0.6, delay: 0.25, dur: 0.6, yaw: [0.08, 0.18], pitch: 0.1 };
function hashRand(seed: number, i: number, j: number): number {
  let x = (seed ^ Math.imul(i + 1, 0x9e3779b1) ^ Math.imul(j + 1, 0x85ebca77)) >>> 0;
  x = Math.imul(x ^ (x >>> 16), 0x7feb352d); x = Math.imul(x ^ (x >>> 15), 0x846ca68b); x ^= x >>> 16;
  return (x >>> 0) / 4294967296;
}
interface Wander { yaw: number; pitch: number; delay: number; dur: number }
function wanderFor(k: GestureKey, to: GesturePose, seed: number | undefined, i: number): Wander | null {
  if (seed === undefined || k.hold < WANDER.minHold || k.ease === 'blink' || k.ease === 'snap' || k.eye === 'dash') return null;
  if (hashRand(seed, i, 0) >= WANDER.chance) return null;
  const sign = Math.abs(to.yaw) > 0.3 ? -Math.sign(to.yaw) : hashRand(seed, i, 1) < 0.5 ? -1 : 1;
  const yaw = sign * (WANDER.yaw[0] + (WANDER.yaw[1] - WANDER.yaw[0]) * hashRand(seed, i, 2));
  const pitch = (hashRand(seed, i, 3) * 2 - 1) * WANDER.pitch;
  const delay = Math.max(0.2, WANDER.delay * k.hold);
  return { yaw, pitch, delay, dur: Math.min(WANDER.dur, k.hold - delay) };
}

/** The head at `t` seconds into a clip that starts from pose `from` (the last clip's end, so clips chain). Pure.
 *  Past the end it stays on the clip's last pose. */
export function gestureFrame(keys: GestureKey[], t: number, from: GesturePose, seed?: number): GesturePose {
  let prev = from, at = 0;
  for (let i = 0; i < keys.length; i++) {
    const k = keys[i], to = keyPose(k), mv = moveTiming(k, prev, to);
    if (t < at + mv) {
      const p = Math.max(0, (t - at) / mv), e = k.ease ?? 'glide';
      const f = mix(prev, to, e === 'glide' || e === 'back' ? land(p) : ease[e](p));
      if (e === 'back') f.s = prev.s + (to.s - prev.s) * ease.back(p);   // only the body springs past
      if (k.scan) {
        // each cycle: a slow glide left -> right, then a quick return right -> left, both eased (no jump at the turns)
        const { cycles, yaw: A, back } = k.scan, q = Math.min(cycles, p * cycles) % 1 || (p >= 1 ? 1 : 0);
        const go = 1 - back, sm = (x: number) => x * x * (3 - 2 * x);
        f.yaw = q < go ? -A + 2 * A * sm(q / go) : A - 2 * A * sm((q - go) / back);
      }
      return f;
    }
    at += mv;
    const w = wanderFor(k, to, seed, i);
    const held = (th: number): GesturePose => {
      if (!w) return to;
      const q = land(Math.max(0, Math.min(1, (th - w.delay) / w.dur)));
      return { ...to, yaw: to.yaw + w.yaw * q, pitch: to.pitch + w.pitch * q };
    };
    if (t < at + k.hold) return held(t - at);
    at += k.hold;
    prev = held(k.hold);
  }
  return prev;
}

/** How long a clip runs from `from` (moves depend on how far they turn). */
export function clipDur(keys: GestureKey[], from: GesturePose = NEUTRAL): number {
  let prev = from, at = 0;
  for (const k of keys) { const to = keyPose(k); at += moveTiming(k, prev, to) + k.hold; prev = to; }
  return at;
}

/** A clip's last pose with the head's turns folded back into -PI..PI (a full roll or spin ends where it began). */
export function settle(p: GesturePose): GesturePose {
  const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
  return { ...p, yaw: wrap(p.yaw), pitch: wrap(p.pitch), roll: wrap(p.roll) };
}

/** The picker remembers what played recently (not a fixed cycle): the last gesture never comes
 *  straight back, and the ones before it are damped (RECENT[i] multiplies the weight of the gesture i+1 picks ago),
 *  so it avoids A-B-A and short loops while Roll stays the most common. Reading never repeats its last version. */
export const RECENT = [0, 0.25, 0.5, 0.75];
/** Add `name` to the picker's memory (most recent last, the last HISTORY kept). The last reading version is always
 *  kept (in the oldest slot, which RECENT doesn't weigh), so reading never repeats its version however long ago it was. */
export const HISTORY = 8;
export function remember(history: GestureName[], name: GestureName): GestureName[] {
  const next = history.concat(name).slice(-HISTORY);
  if (!next.some((h) => READING.includes(h))) {
    const lastRead = [...history].reverse().find((h) => READING.includes(h));
    if (lastRead) next[0] = lastRead;
  }
  return next;
}

/** A weighted random clip given what played before (`history`, most recent last; a single name or null also works)
 *  and what the agent is doing (`activity`). rand: 0..1. */
export function pickGesture(rand: () => number, history: GestureName[] | GestureName | null, activity: Activity = 'none'): GestureName {
  const hist = history == null ? [] : Array.isArray(history) ? history : [history];
  const picks = hist.map(pickOf), tilt = ACTIVITY[activity] || {};
  const weight = (k: PickName) => {
    let w = WEIGHTS[k] * (tilt[k] ?? 1);
    for (let i = 0; i < RECENT.length; i++) if (picks[picks.length - 1 - i] === k) w *= RECENT[i];
    return w;
  };
  const keys = PICK_KEYS.filter((k) => weight(k) > 0);
  const total = keys.reduce((a, k) => a + weight(k), 0);
  let r = rand() * total, pick = keys[keys.length - 1];
  for (const k of keys) { r -= weight(k); if (r < 0) { pick = k; break; } }
  if (pick !== 'reading') return pick;
  const lastRead = [...hist].reverse().find((h) => READING.includes(h));
  const versions = READING.filter((v) => v !== lastRead), bias = READING_BIAS[activity] || {};
  const vt = versions.reduce((a, v) => a + (bias[v] ?? 1), 0);
  let rv = rand() * vt;
  for (const v of versions) { rv -= bias[v] ?? 1; if (rv < 0) return v; }
  return versions[versions.length - 1];
}

/** The working row's entrance, each time it mounts: from ENTRANCE.from a tiny faint dot (startScale of full size)
 *  scales up and fades in together, ease-out, full by ENTRANCE.full, no overshoot. */
export const ENTRANCE = { from: 0.033, full: 0.167, startScale: 0.2 };
export function entranceFrame(t: number): { scale: number; alpha: number } {
  if (t < ENTRANCE.from) return { scale: 0, alpha: 0 };
  const k = Math.min(1, (t - ENTRANCE.from) / (ENTRANCE.full - ENTRANCE.from)), e = 1 - (1 - k) * (1 - k) * (1 - k);
  return { scale: ENTRANCE.startScale + (1 - ENTRANCE.startScale) * e, alpha: 0.25 + 0.75 * e };
}

/** Where one eye lands for a head pose, as shares of A from the face centre (before the body's own dx/dy): its centre
 *  and the 2x2 projection of its tangent frame (`m` = [right.x, right.y, down.x, down.y]: draw the eye in its own
 *  units through it and it foreshortens, tilts and narrows toward the rim exactly as on a sphere). Null when it is
 *  round the back. sg: -1 left eye, +1 right. */
export function eyeOnSphere(g: GesturePose, sg: number): { x: number; y: number; m: [number, number, number, number]; fore: number } | null {
  const a0 = Math.asin(2 * EYE_X);                       // EYE_X of A = 2 * EYE_X of the radius
  const cy = Math.cos(g.yaw), sy = Math.sin(g.yaw), cp = Math.cos(g.pitch), sp = Math.sin(g.pitch), cr = Math.cos(g.roll), sr = Math.sin(g.roll);
  const cl = Math.cos(g.lat ?? 0), sl = Math.sin(g.lat ?? 0);
  // the latitude tilt first (so a yaw keeps the eyes' height), then yaw about the vertical axis, then pitch about the
  // horizontal, then roll about the view axis
  const rot = (v: [number, number, number]): [number, number, number] => {
    let [x, y, z] = v;
    [y, z] = [y * cl + z * sl, -y * sl + z * cl];
    [x, z] = [x * cy + z * sy, -x * sy + z * cy];
    [y, z] = [y * cp + z * sp, -y * sp + z * cp];
    [x, y] = [x * cr - y * sr, x * sr + y * cr];
    return [x, y, z];
  };
  const c = rot([sg * Math.sin(a0), 0, Math.cos(a0)]);
  if (c[2] <= 0.02) return null;
  const r = rot([Math.cos(a0), 0, -sg * Math.sin(a0)]), d = rot([0, 1, 0]);
  return { x: c[0] / 2, y: c[1] / 2, m: [r[0], r[1], d[0], d[1]], fore: c[2] };
}

/** The sphere the eyes live on, in the body's unit space: centred at the shape's faceY, radius from its shorter
 *  half-extent and faceScale (so short shapes such as the pill keep the eyes inside). */
export function faceSphere(s: { w: number; h: number; faceY?: number; faceScale?: number }): { cy: number; r: number } {
  return { cy: s.faceY || 0, r: Math.min(s.w, s.h) * (s.faceScale || 1) };
}
