/*
 * Living Avatars core: the platform-neutral model, ported line for line from the prototype's
 * avatar-core.js (keep the two in step; the Swift port follows the same file).
 *
 * No DOM, no canvas, no React. Everything here is plain numbers and tables so
 * it can be mirrored line for line in Swift. Renderers (avatarCanvas.ts today,
 * a SwiftUI Canvas later) only read the Pose that AvatarModel.step() returns.
 *
 * Coordinates: unit space, origin at the body centre, +y is down,
 * 1.0 ~= the body's half height. Angles are radians, -PI/2 is straight up.
 */

export type Pt = [number, number];
type Vert = [number, number, number];

// ---------------------------------------------------------------- shapes
// Two kinds of shape:
//  * superellipse (w, h, n) with optional modifiers:
//      taper  >0 widens the bottom, <0 widens the top
//      lean   shifts the top sideways
//      bumps  [angle, amplitude, width] gaussian bulges on the outline (ears, puffs)
//      lobes  [count, amplitude] petal ripple around the whole outline
//  * poly: a list of [x, y, cornerRadius] vertices, each corner rounded with a
//    circular arc (convex or concave), then resampled evenly. Used for the
//    geometric shapes (hexagon, pill, heart, droplet, star).
// Shared options:
//   antenna             draw a stalk with a ball on top
//   faceY / faceScale   where the face sits and how big it is
//   look                how far the eyes travel when looking around (default 1);
//                       lowered on tight shapes so the eyes never leave the body
// h is the body's half height (the renderer anchors breathing at y = +h);
// poly shapes get h filled in from their outline.
export interface ShapeDef {
  w: number; h: number; n?: number;
  taper?: number; lean?: number;
  bumps?: [number, number, number][]; lobes?: [number, number];
  poly?: Vert[];
  antenna?: boolean; faceY?: number; faceScale?: number; look?: number;
}

const TAU = Math.PI * 2;
const ring = (n: number, r: number, start: number, rc: number, dy = 0): Vert[] => Array.from({ length: n }, (_, i) => {
  const a = start + (i / n) * TAU; return [r * Math.cos(a), r * Math.sin(a) + dy, rc];
});
// tangent point on a circle (cx, cy, r) seen from point (px, py); side +1 / -1
function tangentAngle(cx: number, cy: number, r: number, px: number, py: number, side: number): number {
  const dx = px - cx, dy = py - cy, d = Math.hypot(dx, dy);
  return Math.atan2(dy, dx) + side * Math.acos(r / d);
}
function arcPts(cx: number, cy: number, r: number, a0: number, a1: number, steps: number): Vert[] {
  const out: Vert[] = [];
  for (let i = 1; i < steps; i++) { const a = a0 + (a1 - a0) * (i / steps); out.push([cx + r * Math.cos(a), cy + r * Math.sin(a), 0]); }
  return out;
}
function heartPoly(): Vert[] {
  const cx = 0.46, cy = -0.26, r = 0.5, tipY = 0.9;
  const cuspY = cy - Math.sqrt(r * r - cx * cx);
  const aCusp = Math.atan2(cuspY - cy, 0 - cx);            // where the right lobe meets the cusp
  let aTan = tangentAngle(cx, cy, r, 0, tipY, -1);          // outer tangent toward the tip
  while (aTan < aCusp) aTan += TAU;
  const right = arcPts(cx, cy, r, aCusp, aTan, 28);
  right.push([cx + r * Math.cos(aTan), cy + r * Math.sin(aTan), 0]);
  const left = right.slice().reverse().map(([x, y, c]): Vert => [-x, y, c]);
  return [[0, cuspY + 0.04, 0.1], ...right, [0, tipY, 0.2], ...left];
}
function dropletPoly(): Vert[] {
  const cy = 0.26, r = 0.62, tipY = -0.96;
  let aR = tangentAngle(0, cy, r, 0, tipY, 1);              // upper-right tangent
  const aL = Math.PI - aR;
  if (aR > aL) aR -= TAU;
  const pts: Vert[] = [];
  pts.push([r * Math.cos(aR), cy + r * Math.sin(aR), 0]);
  pts.push(...arcPts(0, cy, r, aR, aL, 40));
  pts.push([r * Math.cos(aL), cy + r * Math.sin(aL), 0]);
  return [[0, tipY, 0.2], ...pts];
}
function starPoly(): Vert[] {
  const pts: Vert[] = [];
  for (let i = 0; i < 10; i++) {
    const outer = i % 2 === 0, a = -Math.PI / 2 + (i / 10) * TAU;
    const r = outer ? 1.0 : 0.6;
    pts.push([r * Math.cos(a), r * Math.sin(a) + 0.1, outer ? 0.22 : 0.16]);
  }
  return pts;
}
// w / h on poly shapes are filled in from the outline below (0 until then).
export const SHAPES: Record<string, ShapeDef> = {
  circle:  { w: 0.84, h: 0.84, n: 2.0, faceY: 0.04 },
  square:  { w: 0.80, h: 0.78, n: 4.6, faceY: 0.04 },
  pill:    { w: 0, h: 0, poly: [[-0.92, -0.58, 0.57], [0.92, -0.58, 0.57], [0.92, 0.58, 0.57], [-0.92, 0.58, 0.57]], faceY: 0.0, look: 0.8 },
  hexagon: { w: 0, h: 0, poly: ring(6, 0.92, 0, 0.2), faceY: 0.04, faceScale: 0.96 },
  heart:   { w: 0, h: 0, poly: heartPoly(), faceY: -0.15, faceScale: 0.82, look: 0.55 },
  droplet: { w: 0, h: 0, poly: dropletPoly(), faceY: 0.28, faceScale: 0.82, look: 0.6 },
  star:    { w: 0, h: 0, poly: starPoly(), faceY: 0.12, faceScale: 0.84, look: 0.55 },
  pebble:  { w: 0.86, h: 0.74, n: 2.6, faceY: 0.06 },
  gumdrop: { w: 0.80, h: 0.84, n: 2.8, taper: 0.22, faceY: 0.12, look: 0.8 },
  bean:    { w: 0.90, h: 0.70, n: 2.4, lean: 0.07, faceY: 0.02, look: 0.85 },
  cloud:   { w: 0.88, h: 0.70, n: 2.2, bumps: [[-2.25, 0.11, 0.32], [-1.57, 0.14, 0.34], [-0.89, 0.11, 0.32]], faceY: 0.03 },
  bunny:   { w: 0.74, h: 0.72, n: 2.4, bumps: [[-1.98, 0.48, 0.12], [-1.16, 0.48, 0.12]], faceY: 0.10, faceScale: 0.96, look: 0.7 },
  kitty:   { w: 0.82, h: 0.72, n: 3.2, bumps: [[-2.28, 0.26, 0.16], [-0.86, 0.26, 0.16]], faceY: 0.08 },
  egg:     { w: 0.72, h: 0.86, n: 2.2, taper: -0.18, faceY: 0.06, faceScale: 0.9, look: 0.8 },
  flower:  { w: 0.82, h: 0.80, n: 2.0, lobes: [6, 0.07], faceY: 0.04, faceScale: 0.94 },
  sprout:  { w: 0.68, h: 0.84, n: 3.0, antenna: true, faceY: 0.06, faceScale: 0.86, look: 0.8 },
};
export const SHAPE_KEYS = Object.keys(SHAPES);

function angDiff(a: number, b: number): number {
  let d = a - b;
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d < -Math.PI) d += 2 * Math.PI;
  return d;
}

function shapePoint(s: ShapeDef, t: number): Pt {
  const c = Math.cos(t), sn = Math.sin(t), e = 2 / (s.n as number);
  let x = s.w * Math.sign(c) * Math.pow(Math.abs(c), e);
  let y = s.h * Math.sign(sn) * Math.pow(Math.abs(sn), e);
  let f = 1;
  if (s.bumps) for (const [a, amp, wd] of s.bumps) {
    const d = angDiff(t, a);
    f += amp * Math.exp(-(d * d) / (2 * wd * wd));
  }
  if (s.lobes) f *= 1 + s.lobes[1] * Math.cos(s.lobes[0] * (t + Math.PI / 2));
  x *= f; y *= f;
  if (s.taper) x *= 1 + s.taper * (y / s.h);
  if (s.lean) x -= s.lean * (y / s.h);
  return [x, y];
}

// Round every corner of a closed polygon with its own radius, returning a
// dense polyline (arcs sampled every few degrees).
function roundPoly(verts: Vert[]): Pt[] {
  const n = verts.length, out: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const [px, py, rc] = verts[i], A = verts[(i + n - 1) % n], B = verts[(i + 1) % n];
    if (!rc) { out.push([px, py]); continue; }
    let ax = A[0] - px, ay = A[1] - py, bx = B[0] - px, by = B[1] - py;
    const la = Math.hypot(ax, ay), lb = Math.hypot(bx, by);
    ax /= la; ay /= la; bx /= lb; by /= lb;
    const th = Math.acos(Math.max(-1, Math.min(1, ax * bx + ay * by)));
    if (th < 1e-3 || th > Math.PI - 1e-3) { out.push([px, py]); continue; }
    let t = rc / Math.tan(th / 2);
    t = Math.min(t, 0.5 * la, 0.5 * lb);
    const r = t * Math.tan(th / 2);
    let mx = ax + bx, my = ay + by; const ml = Math.hypot(mx, my); mx /= ml; my /= ml;
    const dc = r / Math.sin(th / 2), cx = px + mx * dc, cy = py + my * dc;
    const a0 = Math.atan2(py + ay * t - cy, px + ax * t - cx), a1 = Math.atan2(py + by * t - cy, px + bx * t - cx);
    const da = angDiff(a1, a0), steps = Math.max(2, Math.ceil(Math.abs(da) / 0.12));
    for (let k = 0; k <= steps; k++) { const a = a0 + da * (k / steps); out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); }
  }
  return out;
}

// Resample a closed polyline to n evenly spaced points, starting at the top.
function resample(pts: Pt[], n: number): Pt[] {
  let start = 0;
  for (let i = 1; i < pts.length; i++) if (pts[i][1] < pts[start][1] - 1e-9 || (Math.abs(pts[i][1] - pts[start][1]) < 1e-9 && Math.abs(pts[i][0]) < Math.abs(pts[start][0]))) start = i;
  const P = pts.slice(start).concat(pts.slice(0, start)); P.push(P[0]);
  const cum = [0];
  for (let i = 1; i < P.length; i++) cum.push(cum[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]));
  const L = cum[cum.length - 1], out: Pt[] = [];
  let j = 1;
  for (let k = 0; k < n; k++) {
    const d = (k / n) * L;
    while (cum[j] < d) j++;
    const f = (d - cum[j - 1]) / ((cum[j] - cum[j - 1]) || 1);
    out.push([P[j - 1][0] + (P[j][0] - P[j - 1][0]) * f, P[j - 1][1] + (P[j][1] - P[j - 1][1]) * f]);
  }
  return out;
}

// Closed outline as N points; renderers smooth it with midpoint quadratics.
const outlineCache = new Map<string, Pt[]>();
export function shapeOutline(key: string, n = 72): Pt[] {
  const s = SHAPES[key] || SHAPES.pebble;
  if (s.poly) n = Math.max(n, 128);
  const id = key + ':' + n;
  const hit = outlineCache.get(id);
  if (hit) return hit;
  let pts: Pt[];
  if (s.poly) pts = resample(roundPoly(s.poly), n);
  else { pts = []; for (let i = 0; i < n; i++) pts.push(shapePoint(s, -Math.PI / 2 + (i / n) * 2 * Math.PI)); }
  outlineCache.set(id, pts);
  return pts;
}
for (const k of SHAPE_KEYS) {
  const s = SHAPES[k];
  if (s.poly) { const p = shapeOutline(k); s.h = Math.max(...p.map((q) => q[1])); s.w = Math.max(...p.map((q) => Math.abs(q[0]))); }
}

// --------------------------------------------------------------- moods
// Eyes-only faces (no mouth, no brows). Lid model adapted from bbot (MIT,
// bwndapp/bbot on GitHub): each eye is a solid disc cut by a top and
// a bottom lid.
//   lidT / lidB  0..1  how far the top / bottom lid closes (0.5 + 0.5 = shut)
//   tilt   radians      lid rotation; +ve drops the inner edge (cross),
//                       -ve drops the outer edge (sad / worried)
//   size   mult         eye size
//   eyeY   unit space   vertical nudge of both eyes (-ve is up)
//   gap    mult         distance between the eyes
//   head   radians      small tilt of the whole face
export interface Mood { lidT: number; lidB: number; tilt: number; size: number; eyeY: number; gap: number; head: number }
export const MOODS: Record<string, Mood> = {
  neutral:   { lidT: 0.00, lidB: 0.00, tilt:  0.00, size: 1.00, eyeY:  0.00, gap: 1.00, head:  0.00 },
  focused:   { lidT: 0.30, lidB: 0.26, tilt:  0.00, size: 1.04, eyeY:  0.00, gap: 0.94, head:  0.00 },
  attentive: { lidT: 0.00, lidB: 0.00, tilt:  0.00, size: 1.22, eyeY: -0.04, gap: 1.04, head:  0.10 },
  happy:     { lidT: 0.00, lidB: 0.46, tilt:  0.00, size: 1.10, eyeY:  0.02, gap: 1.00, head:  0.00 },
  content:   { lidT: 0.10, lidB: 0.30, tilt:  0.00, size: 1.00, eyeY:  0.02, gap: 0.98, head:  0.04 },
  worried:   { lidT: 0.34, lidB: 0.00, tilt: -0.36, size: 1.04, eyeY:  0.03, gap: 1.00, head: -0.05 },
  sleepy:    { lidT: 0.60, lidB: 0.05, tilt: -0.07, size: 1.00, eyeY:  0.03, gap: 1.00, head:  0.08 },
  asleep:    { lidT: 0.50, lidB: 0.50, tilt:  0.00, size: 1.00, eyeY:  0.04, gap: 1.00, head:  0.10 },
  surprised: { lidT: 0.00, lidB: 0.00, tilt:  0.00, size: 1.32, eyeY: -0.03, gap: 1.03, head:  0.00 },
};
const MOOD_KEYS = Object.keys(MOODS.neutral) as (keyof Mood)[];

// Eye placement shared by every renderer, in unit space before scaling.
// Returns the face origin offset, its rotation and the two eye discs.
export function faceLayout(shapeKey: string, mood: Mood, lookX: number, lookY: number): { fy: number; rot: number; eyes: [number, number, number][] } {
  const s = SHAPES[shapeKey] || SHAPES.pebble;
  const fs = s.faceScale || 1, look = s.look ?? 1;
  const ex = 0.33 * fs * mood.gap, er = 0.19 * fs * mood.size;
  const lx = lookX * 0.15 * fs * look, ly = lookY * 0.11 * fs * look;
  const y = -0.06 * fs + mood.eyeY * fs + ly;
  return { fy: s.faceY || 0, rot: mood.head + lookX * 0.06, eyes: [[-ex + lx, y, er], [ex + lx, y, er]] };
}

// Canvas layout shared by renderers: body unit and centre as fractions of the
// avatar size (no state ring any more, so the body fills more of the box).
export const LAYOUT = { u: 0.355, cy: 0.5 };
// Status dot: sits on the body's bottom-right edge (the outline point furthest
// along the 45° down-right diagonal) with a cut-out ring. It starts with
// its centre just outside the edge and slides further out along the diagonal
// only as far as needed to clear every possible eye position (all moods, look
// limits, head tilt), but never so far that it stops overlapping the body.
// Returns pixels relative to the body centre; cached per shape and size.
export const DOT = { frac: 0.1, min: 2.4, cutFrac: 0.22, pulse: 1.12, margin: 0.75, startOut: 0.3, maxOut: 0.75 };
export interface DotPlace { x: number; y: number; r: number; cut: number; glyph: boolean; clearance: number }
const dotAnchorCache = new Map<string, Pt>(), eyeEnvCache = new Map<string, [number, number, number][]>(), dotCache = new Map<string, DotPlace>();
// side +1: bottom-right (status dot); side -1: bottom-left (provider badge)
function dotAnchor(key: string, side = 1): Pt {
  const id = key + ':' + side;
  let best = dotAnchorCache.get(id);
  if (!best) {
    let bv = -1e9;
    for (const [x, y] of shapeOutline(key)) { const v = side * x + y; if (v > bv) { bv = v; best = [x, y]; } }
    dotAnchorCache.set(id, best as Pt);
  }
  return best as Pt;
}
// every eye disc the face can produce, in body units
function eyeEnvelope(key: string): [number, number, number][] {
  let discs = eyeEnvCache.get(key);
  if (!discs) {
    discs = [];
    for (const mk of Object.keys(MOODS)) for (const lx of [-1, 0, 1]) for (const ly of [-1, 0, 1]) {
      const f = faceLayout(key, MOODS[mk], lx, ly), cs = Math.cos(f.rot), sn = Math.sin(f.rot);
      for (const [ex, ey, er] of f.eyes) discs.push([ex * cs - ey * sn, ex * sn + ey * cs + f.fy, er]);
    }
    eyeEnvCache.set(key, discs);
  }
  return discs;
}
export function statusDot(key: string, size: number, side = 1): DotPlace {
  const id = key + ':' + size + ':' + side;
  const hit = dotCache.get(id);
  if (hit) return hit;
  const u = size * LAYOUT.u, [ax, ay] = dotAnchor(key, side), d = Math.SQRT1_2, dx = side * d;
  const r = Math.max(DOT.min, size * DOT.frac), cut = Math.max(1, r * DOT.cutFrac), R = (r + cut) * DOT.pulse;
  const clear = (t: number) => {
    const x = ax * u + t * dx, y = ay * u + t * d;
    let g = 1e9;
    for (const [ex, ey, er] of eyeEnvelope(key)) g = Math.min(g, Math.hypot(ex * u - x, ey * u - y) - er * u - R);
    return g;
  };
  let t = (r + cut) * DOT.startOut;
  const tMax = (r + cut) * DOT.maxOut;
  while (clear(t) < DOT.margin && t < tMax) t += 0.25;
  // keep it on the canvas
  const room = Math.min(size / 2 - side * ax * u, size * (1 - LAYOUT.cy) - ay * u) - R;
  t = Math.min(t, room / d);
  const res: DotPlace = { x: ax * u + t * dx, y: ay * u + t * d, r, cut, glyph: size >= 40, clearance: clear(t) };
  dotCache.set(id, res);
  return res;
}

// --------------------------------------------------------------- palette
// Two palettes. Bold is the default; pastel is the original set (same order,
// so pastel identities are unchanged from before).
export interface Swatch { name: string; hex: string }
const PASTEL: Swatch[] = [
  { name: 'Coral',  hex: '#F28B6E' }, { name: 'Butter', hex: '#F2C14E' },
  { name: 'Mint',   hex: '#6FCF97' }, { name: 'Sky',    hex: '#5DADE2' },
  { name: 'Lilac',  hex: '#A88BEB' }, { name: 'Rose',   hex: '#EE7FA9' },
  { name: 'Teal',   hex: '#3FB8AF' }, { name: 'Peach',  hex: '#F6A97A' },
  { name: 'Slate',  hex: '#8FA3BF' }, { name: 'Lime',   hex: '#B5D65A' },
];
const BOLD: Swatch[] = [
  { name: 'Red',      hex: '#E5243B' }, { name: 'Orange',  hex: '#FF7A1A' },
  { name: 'Yellow',   hex: '#FFC61A' }, { name: 'Green',   hex: '#1FA855' },
  { name: 'Teal',     hex: '#00A6A0' }, { name: 'Blue',    hex: '#1E6BE6' },
  { name: 'Indigo',   hex: '#4B3FD8' }, { name: 'Purple',  hex: '#8B2FE0' },
  { name: 'Magenta',  hex: '#D61F8C' }, { name: 'Pink',    hex: '#FF4F8B' },
  { name: 'Charcoal', hex: '#2C2E3B' },
];
export const PALETTES: Record<string, Swatch[]> = { bold: BOLD, pastel: PASTEL, all: BOLD.concat(PASTEL) };
// Avatar page swatches: adaptive ink first, then bold. (Hash identities still
// draw from BOLD so existing agents keep their colour.)
export const SWATCHES: Swatch[] = [{ name: 'Ink', hex: 'ink' }].concat(BOLD);
export const PALETTE = PASTEL; // kept for older callers
export const PALETTE_KEYS = Object.keys(PALETTES);

// Eye ink. 'auto' picks whichever has the higher WCAG contrast on the body.
export const INK = { black: '#231F2E', white: '#FFFFFF' };
export type EyeColor = 'auto' | 'black' | 'white';
export const EYE_COLORS: EyeColor[] = ['auto', 'black', 'white'];

// Theme-aware ink: the default avatar colour and the first swatch. Black body in
// the light theme, white body in the dark theme; with eyeColor 'auto' the eyes
// flip to the contrasting ink. Every other palette colour is fixed.
// theme = 'dark' when <html> has the .dark class.
export type Theme = 'light' | 'dark';
export const INK_COLOR: Record<Theme, string> = { light: '#16161A', dark: '#F4F4F5' };
export function resolveColor(color: string, theme: Theme = 'light'): string { return color === 'ink' ? (INK_COLOR[theme] || INK_COLOR.light) : color; }
// body + eye colours a renderer paints for this pose in this theme
export function paint(pose: Pick<Pose, 'color' | 'eye' | 'eyeColor'>, theme: Theme = 'light'): { body: string; eye: string } {
  const body = resolveColor(pose.color, theme);
  return { body, eye: pose.eye || eyeInk(body, pose.eyeColor || 'black') };
}

function hexToRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}
// amt > 0 mixes toward white, < 0 toward black.
export function shade(hex: string, amt: number): string {
  const [r, g, b] = hexToRgb(hex), t = amt < 0 ? 0 : 255, p = Math.abs(amt);
  const m = (c: number) => Math.round(c + (t - c) * p);
  return `rgb(${m(r)},${m(g)},${m(b)})`;
}
const lin = (c: number) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
function luminance(hex: string): number { const [r, g, b] = hexToRgb(hex).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; }
export function contrastRatio(a: string, b: string): number { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
function lab(hex: string): [number, number, number] {
  const [r, g, b] = hexToRgb(hex).map(lin);
  const f = (t: number) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const X = f((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047), Y = f(0.2126 * r + 0.7152 * g + 0.0722 * b), Z = f((0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883);
  return [116 * Y - 16, 500 * (X - Y), 200 * (Y - Z)];
}
// CIE76 colour distance (kept for palette checks).
export function deltaE(a: string, b: string): number { const p = lab(a), q = lab(b); return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]); }
const inkCache = new Map<string, string>();
export function eyeInk(color: string, eyeColor: string = 'black', theme: Theme = 'light'): string {
  if (color === 'ink') color = INK_COLOR[theme] || INK_COLOR.light;
  if (eyeColor === 'white') return INK.white;
  if (eyeColor !== 'auto') return INK.black;
  let ink = inkCache.get(color);
  if (!ink) { ink = contrastRatio(color, INK.white) > contrastRatio(color, INK.black) ? INK.white : INK.black; inkCache.set(color, ink); }
  return ink;
}
// ------------------------------------------------------------- identity
export function fnv1a(str: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0;
}
// Same agent id (and palette) -> same shape and colour on every platform.
export function identityFor(agentId: string | number, palette = 'bold'): { shape: string; color: string; seed: number } {
  const h = fnv1a(String(agentId));
  const list = PALETTES[palette] || BOLD;
  return { shape: SHAPE_KEYS[h % SHAPE_KEYS.length], color: list[(h >>> 8) % list.length].hex, seed: h };
}

function mulberry32(a: number): () => number {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// --------------------------------------------------------------- states
// dot colours are semantic, shared with the Swift port. Each state shows one
// status dot at the bottom-right of the body. Idle is a quiet neutral grey
// (#85858D: >= 3:1 on white, #F7F7F8 and the selected row; >= 4.4:1 on the dark surfaces) with no glyph; the others carry
// a glyph at 40 px and up. Which roles show the dot at all: see dotFor().
export type AvatarState = 'working' | 'needs_input' | 'finished' | 'failed' | 'idle';
export type Glyph = 'dots' | 'question' | 'check' | 'alert' | null;
export type ReactionName = 'nod' | 'shake' | 'bounce' | 'spin' | 'jump' | 'wink';
interface StateDef {
  label: string; mood: string; dot: string; glyph: Glyph; blink: [number, number]; bob: number; bobHz: number;
  gaze: 'work' | 'viewer' | 'free' | 'down';
  enter?: ReactionName; repeat?: ReactionName; repeatEvery?: number; pulse?: boolean;
  settleMood?: string; settleAfter?: number; sleeps?: boolean;
}
export const STATES: Record<AvatarState, StateDef> = {
  working:     { label: 'Working',     mood: 'focused',   dot: '#3B82F6', glyph: 'dots',     blink: [1.1, 2.4], bob: 0.025, bobHz: 2.2, gaze: 'work' },
  needs_input: { label: 'Needs input', mood: 'attentive', dot: '#F59E0B', glyph: 'question', blink: [2.5, 5.0], bob: 0.010, bobHz: 1.0, gaze: 'viewer',
                 enter: 'nod', repeat: 'nod', repeatEvery: 2.8, pulse: true },
  finished:    { label: 'Finished',    mood: 'happy',     dot: '#22C55E', glyph: 'check',    blink: [2.5, 5.0], bob: 0, bobHz: 0, gaze: 'free',
                 enter: 'bounce', settleMood: 'content', settleAfter: 3.5 },
  failed:      { label: 'Failed',      mood: 'worried',   dot: '#EF4444', glyph: 'alert',    blink: [3.0, 6.0], bob: 0, bobHz: 0, gaze: 'down',
                 enter: 'shake' },
  idle:        { label: 'Idle',        mood: 'neutral',   dot: '#85858D', glyph: null,       blink: [2.2, 5.0], bob: 0, bobHz: 0, gaze: 'free', sleeps: true },
};
export const STATE_KEYS = Object.keys(STATES) as AvatarState[];
const isState = (s: unknown): s is AvatarState => typeof s === 'string' && s in STATES;

// ------------------------------------------------------------ reactions
// Each returns offsets for progress p in [0,1]: dx, dy (unit space), rot, sx, sy, lookY.
interface Offsets { dx?: number; dy?: number; rot?: number; sx?: number; sy?: number; lookX?: number; lookY?: number; wink?: number }
export const REACTIONS: Record<ReactionName, { dur: number; fn: (p: number) => Offsets }> = {
  nod:    { dur: 0.75, fn: (p) => { const k = Math.sin(p * Math.PI * 4) * (1 - p); return { dy: 0.05 * k, lookY: 0.5 * Math.max(0, k), sy: 1 - 0.03 * k }; } },
  shake:  { dur: 0.75, fn: (p) => { const k = Math.sin(p * Math.PI * 6) * (1 - p); return { dx: 0.07 * k, rot: 0.06 * k, lookX: -0.6 * k }; } },
  bounce: { dur: 0.85, fn: (p) => {
    if (p < 0.6) { const h = Math.sin(Math.PI * p / 0.6); return { dy: -0.2 * h, sy: 1 + 0.06 * h, sx: 1 - 0.04 * h }; }
    const s = Math.sin(Math.PI * (p - 0.6) / 0.4) * 0.09; return { sy: 1 - s, sx: 1 + s, dy: s * 0.5 };
  } },
  spin:   { dur: 0.95, fn: (p) => { const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; return { rot: 2 * Math.PI * e, dy: -0.06 * Math.sin(Math.PI * p) }; } },
  jump:   { dur: 0.50, fn: (p) => { const h = Math.sin(Math.PI * p); return { dy: -0.13 * h, sy: 1 + 0.07 * h, sx: 1 - 0.05 * h }; } },
  // a greeting: one eye closes and holds a beat while the head tips toward it
  wink:   { dur: 0.95, fn: (p) => { const c = p < 0.25 ? p / 0.25 : p < 0.6 ? 1 : 1 - (p - 0.6) / 0.4; const e = c * c * (3 - 2 * c);
    return { wink: e, rot: 0.07 * e, lookX: 0.25 * e, dy: -0.02 * e }; } },
};

const clamp = (v: number, a: number, b: number) => (v < a ? a : v > b ? b : v);

// ---------------------------------------------------------------- model
export interface Pose {
  dx: number; dy: number; rot: number; sx: number; sy: number; lookX: number; lookY: number; lid: number;
  /** 0-1: how far the second eye is closed by a wink (both eyes blink with `lid`). */
  wink?: number;
  mood: Mood; shape: string; color: string; state: AvatarState; dot: string; glyph: Glyph;
  eyeColor: EyeColor; eye: string | null; phase: number; pulse: number | null; asleep: boolean; zzz: number | null; reducedMotion: boolean;
}
export interface Pointer { dx: number; dy: number }
export interface ModelOpts {
  agentId?: string | number; palette?: string; eyeColor?: string; shape?: string; color?: string; seed?: number;
  sleepAfterMs?: number; followPointer?: boolean; wander?: boolean; reducedMotion?: boolean; state?: string;
  /** No constant motion (no breathing, no bob): blinks, glances and one-off reactions only. */
  calm?: boolean;
}

export class AvatarModel {
  agentId: string | number;
  palette: string;
  eyeColor: EyeColor;
  shape: string;
  colorPinned: boolean;
  color: string;
  rand: () => number;
  sleepAfterMs: number;
  followPointer: boolean;
  wander: boolean;
  calm: boolean;
  /** Until this time the eyes hold a glance given by glance(); the renderer lets it win over the pointer. */
  glanceUntil: number;
  /** The renderer does not follow the pointer before this time (a hello reaction plays out first). */
  followAfter: number;
  reducedMotion: boolean;
  state: AvatarState;
  t: number;
  phase: number;
  stateSince: number;
  asleep: boolean;
  surprisedUntil: number;
  reaction: { name: ReactionName; t0: number } | null;
  mood: Mood;
  look: { x: number; y: number };
  lookTarget: { x: number; y: number };
  nextGlance: number;
  blinkAt: number;
  nextBlink: number;
  nextRepeat: number;
  version: number;
  // the ticker's per-frame cache for a shared model (one step per frame however many instances show it)
  __frame?: number;
  __pose?: Pose;
  __seen?: boolean;

  constructor(opts: ModelOpts = {}) {
    this.agentId = opts.agentId ?? Math.random();
    this.palette = opts.palette && PALETTES[opts.palette] ? opts.palette : 'bold';
    this.eyeColor = EYE_COLORS.includes(opts.eyeColor as EyeColor) ? opts.eyeColor as EyeColor : 'black';
    const id = identityFor(this.agentId, this.palette);
    this.shape = opts.shape || id.shape;
    this.colorPinned = !!opts.color;
    this.color = opts.color || id.color;
    this.rand = mulberry32(opts.seed ?? id.seed);
    this.sleepAfterMs = opts.sleepAfterMs ?? 30000;
    this.followPointer = opts.followPointer ?? true;
    this.wander = opts.wander ?? true; // idle glances around; dashboards limit this to the selected sidebar avatar
    this.reducedMotion = !!opts.reducedMotion;
    this.calm = !!opts.calm;
    this.glanceUntil = -1;
    this.followAfter = 0;
    this.state = isState(opts.state) ? opts.state : 'idle';
    this.t = this.rand() * 10; // de-sync breathing across a fleet
    this.phase = this.rand() * Math.PI * 2;
    this.stateSince = this.t;
    this.asleep = false;
    this.surprisedUntil = 0;
    this.reaction = null;
    this.mood = { ...MOODS[STATES[this.state].mood] };
    this.look = { x: 0, y: 0 };
    this.lookTarget = { x: 0, y: 0 };
    this.nextGlance = this.t + 1 + this.rand() * 2;
    this.blinkAt = -1;
    this.nextBlink = this.t + this.rand() * 3;
    this.nextRepeat = Infinity;
    this.version = 0; // bumps whenever a static (reduced motion) frame must redraw
  }

  configure(o: { shape?: string; sleepAfterMs?: number; followPointer?: boolean; reducedMotion?: boolean; wander?: boolean; calm?: boolean; eyeColor?: string; palette?: string; color?: string }): void {
    for (const k of ['shape', 'sleepAfterMs', 'followPointer', 'reducedMotion', 'wander', 'calm'] as const) {
      const v = o[k];
      if (v !== undefined && v !== this[k]) { (this as unknown as Record<string, unknown>)[k] = v; this.version++; }
    }
    if ('eyeColor' in o) {
      const e = EYE_COLORS.includes(o.eyeColor as EyeColor) ? o.eyeColor as EyeColor : 'black';
      if (e !== this.eyeColor) { this.eyeColor = e; this.version++; }
    }
    if (o.palette !== undefined && PALETTES[o.palette] && o.palette !== this.palette) { this.palette = o.palette; this.version++; }
    // an explicit colour pins it; no colour means "hash from the palette"
    if ('color' in o) this.colorPinned = !!o.color;
    const c = this.colorPinned ? (o.color || this.color) : identityFor(this.agentId, this.palette).color;
    if (c !== this.color) { this.color = c; this.version++; }
    if (this.reducedMotion) this.asleep = false;
  }

  setState(next: string): void {
    if (!isState(next) || next === this.state) return;
    this.state = next;
    this.stateSince = this.t;
    this.version++;
    const def = STATES[next];
    this.nextRepeat = def.repeat ? this.t + (def.repeatEvery as number) : Infinity;
    if (this.asleep) { this.wake(); return; }
    if (def.enter) this.react(def.enter);
  }

  react(name: string): void {
    if (!(name in REACTIONS) || this.reducedMotion) return;
    this.reaction = { name: name as ReactionName, t0: this.t };
  }

  wake(): void {
    this.asleep = false;
    this.stateSince = this.t;
    this.surprisedUntil = this.t + 0.9;
    this.react('jump');
  }

  // Kept awake: the selected agent's avatar wakes if it had dozed off (it noticed you), and its sleep clock
  // restarts, so it stays awake, wandering and following the cursor, for as long as it is selected.
  notice(): void {
    if (this.reducedMotion) return;
    if (this.asleep) { this.wake(); return; }
    if (STATES[this.state].sleeps) this.stateSince = this.t;
  }

  // Look at something for a moment (x, y in look units: -1..1), then go back to the usual gaze. Only a free
  // gaze takes it: working glances and an attentive look at the viewer keep their own.
  glance(x: number, y: number, holdS = 1.5): void {
    if (this.reducedMotion || this.asleep || STATES[this.state].gaze !== 'free') return;
    this.lookTarget.x = clamp(x, -1, 1); this.lookTarget.y = clamp(y, -1, 1);
    this.nextGlance = this.t + holdS;
    this.glanceUntil = this.t + holdS;
  }

  // Tap / click: wake if asleep, otherwise acknowledge with a nod.
  poke(): void {
    if (this.asleep) this.wake();
    else { this.react('nod'); if (this.state === 'idle') this.stateSince = this.t; }
  }

  targetMoodKey(): string {
    const def = STATES[this.state];
    if (this.asleep) return 'asleep';
    if (this.t < this.surprisedUntil) return 'surprised';
    const age = this.t - this.stateSince;
    if (def.settleMood && age > (def.settleAfter as number)) return def.settleMood;
    if (def.sleeps && this.sleepAfterMs > 0 && !this.reducedMotion && age > 0.75 * this.sleepAfterMs / 1000) return 'sleepy';
    return def.mood;
  }

  /**
   * Advance by dt seconds.
   * pointer: { dx, dy } offset of the pointer from the avatar centre measured
   *          in avatar sizes, or null when there is no recent pointer.
   * Returns the Pose a renderer draws.
   */
  step(dt: number, pointer: Pointer | null): Pose {
    const def = STATES[this.state];
    if (this.reducedMotion) return this.staticPose();

    this.t += dt;
    const t = this.t;

    // sleep
    if (def.sleeps && !this.asleep && this.sleepAfterMs > 0 && t - this.stateSince > this.sleepAfterMs / 1000) this.asleep = true;

    // repeat cue (needs input keeps nodding)
    if (t >= this.nextRepeat && def.repeat) { this.react(def.repeat); this.nextRepeat = t + (def.repeatEvery as number); }

    // mood blend
    const target = MOODS[this.targetMoodKey()];
    const k = 1 - Math.exp(-dt * 7);
    for (const key of MOOD_KEYS) this.mood[key] += (target[key] - this.mood[key]) * k;

    // gaze
    if (this.asleep) { this.lookTarget.x = 0; this.lookTarget.y = 0.2; }
    // pointer follow is opt-in per frame (the renderer only passes a pointer when
    // followAllowed() says so) and never overrides working glances
    else if (pointer && this.followPointer && def.gaze !== 'work') {
      const d = Math.hypot(pointer.dx, pointer.dy) || 1, mag = Math.min(1, d / 2);
      this.lookTarget.x = (pointer.dx / d) * mag;
      this.lookTarget.y = (pointer.dy / d) * mag;
    } else if (def.gaze === 'viewer') { this.lookTarget.x = 0; this.lookTarget.y = 0; }
    else if (def.gaze === 'free' && !this.wander) { this.lookTarget.x = 0; this.lookTarget.y = 0; }
    else if (t >= this.nextGlance) {
      const r = this.rand;
      if (def.gaze === 'work') { this.lookTarget.x = -0.5 + r() * 1.0; this.lookTarget.y = 0.45 + r() * 0.2; this.nextGlance = t + 0.35 + r() * 0.5; }
      else if (def.gaze === 'down') { this.lookTarget.x = -0.3 + r() * 0.6; this.lookTarget.y = 0.5; this.nextGlance = t + 2 + r() * 2; }
      else if (r() < 0.35) { this.lookTarget.x = 0; this.lookTarget.y = 0; this.nextGlance = t + 1.5 + r() * 2.5; }
      else { this.lookTarget.x = -0.8 + r() * 1.6; this.lookTarget.y = -0.5 + r() * 0.9; this.nextGlance = t + 1.2 + r() * 2.5; }
    }
    const lk = 1 - Math.exp(-dt * 14);
    this.look.x += (this.lookTarget.x - this.look.x) * lk;
    this.look.y += (this.lookTarget.y - this.look.y) * lk;

    // blink
    let lid = 1;
    if (!this.asleep) {
      if (t >= this.nextBlink) {
        this.blinkAt = t;
        const [a, b] = def.blink;
        this.nextBlink = this.rand() < 0.15 ? t + 0.28 : t + a + this.rand() * (b - a);
      }
      const bp = (t - this.blinkAt) / 0.16;
      if (bp >= 0 && bp <= 1) lid = 1 - Math.sin(Math.PI * bp);
    }

    // body motion
    const breathHz = this.asleep ? 1 / 5 : 1 / 3.6, breathAmp = this.calm ? 0 : this.asleep ? 0.035 : 0.018;
    const br = Math.sin(2 * Math.PI * breathHz * t + this.phase);
    const pose = {
      dx: 0, dy: 0, rot: 0,
      sx: 1 - breathAmp * 0.45 * br, sy: 1 + breathAmp * br,
      lookX: this.look.x, lookY: this.look.y, lid,
    };
    if (def.bob && !this.asleep && !this.calm) pose.dy += def.bob * Math.sin(2 * Math.PI * def.bobHz * t + this.phase);

    let wink = 0;
    if (this.reaction) {
      const R = REACTIONS[this.reaction.name], p = (t - this.reaction.t0) / R.dur;
      if (p >= 1) this.reaction = null;
      else {
        const o = R.fn(p);
        pose.dx += o.dx || 0; pose.dy += o.dy || 0; pose.rot += o.rot || 0;
        pose.sx *= o.sx || 1; pose.sy *= o.sy || 1;
        pose.lookX = clamp(pose.lookX + (o.lookX || 0), -1, 1);
        pose.lookY = clamp(pose.lookY + (o.lookY || 0), -1, 1);
        wink = o.wink || 0;
      }
    }

    const out = this.decorate(pose, def);
    out.wink = wink;
    return out;
  }

  // Reduced motion, Live Activities, complications: one still frame per state.
  staticPose(): Pose {
    const def = STATES[this.state];
    this.mood = { ...MOODS[def.settleMood || def.mood] };
    return this.decorate({ dx: 0, dy: 0, rot: 0, sx: 1, sy: 1, lookX: 0, lookY: 0, lid: 1 }, def);
  }

  // Same still frame without touching the live model (pill role, shared models). `dozing`: an idle agent
  // drawn asleep without motion (reduced motion still shows who is asleep; closed eyes do not move).
  stillPose(dozing = false): Pose {
    const def = STATES[this.state];
    const pose = this.decorate({ dx: 0, dy: 0, rot: 0, sx: 1, sy: 1, lookX: 0, lookY: 0, lid: 1 }, def);
    const sleeping = dozing && !!def.sleeps;
    pose.mood = { ...MOODS[sleeping ? 'asleep' : def.settleMood || def.mood] };
    pose.phase = 0; pose.pulse = null; pose.asleep = sleeping; pose.zzz = null;
    return pose;
  }

  // First time on screen: an idle agent that is not the selected one dozes off within a few seconds instead
  // of staying awake for the whole sleep delay after every page load (its status, if it is busy, arrives in
  // that time and keeps it awake).
  settleAsleepSoon(afterS = 3): void {
    if (!STATES[this.state].sleeps || this.sleepAfterMs <= 0 || this.asleep) return;
    this.stateSince = Math.min(this.stateSince, this.t - Math.max(0, this.sleepAfterMs / 1000 - afterS));
  }

  decorate(pose: Pick<Pose, 'dx' | 'dy' | 'rot' | 'sx' | 'sy' | 'lookX' | 'lookY' | 'lid'>, def: StateDef): Pose {
    return Object.assign(pose, {
      mood: this.mood,
      shape: this.shape,
      color: this.color,
      state: this.state,
      dot: def.dot,
      glyph: def.glyph,
      eyeColor: this.eyeColor,
      eye: this.color === 'ink' ? null : eyeInk(this.color, this.eyeColor), // ink resolves per theme in paint()
      phase: this.reducedMotion ? 0 : this.t,
      pulse: def.pulse && !this.reducedMotion ? (this.t * 0.9) % 1 : null,
      asleep: this.asleep,
      zzz: this.asleep ? (this.t * 0.45) % 1 : null,
      reducedMotion: this.reducedMotion,
    });
  }
}

// ---------------------------------------------------------------- roles
// Where an avatar sits decides what it shows. Pure rules, shared with tests
// and with the Swift port.
//   sidebar   agent row: dot always (idle = grey), glyph >= 40 px, badge,
//             animates; idle wander + eye-follow only when selected
//   pill      top pill, static (no wander). Dot only at phone width and only
//             while working / needs input, plain colour, no glyph
//   activity  bottom-left working blurb: animates in sync with the sidebar,
//             never a dot, no badge
//   drawer    large face (drawer, home hero, picker): never a dot; badge;
//             idle wander + eye-follow when selected
export type Role = 'sidebar' | 'pill' | 'activity' | 'drawer';
interface RoleDef { dot: 'always' | 'phone-working' | 'never'; glyph: boolean; badge: boolean; animate: boolean; wander: 'selected' | false; follow: boolean }
export const ROLES: Record<Role, RoleDef> = {
  sidebar:  { dot: 'always',        glyph: true,  badge: true,  animate: true,  wander: 'selected', follow: true },
  pill:     { dot: 'phone-working', glyph: false, badge: true,  animate: false, wander: false,      follow: false },
  activity: { dot: 'never',         glyph: false, badge: false, animate: true,  wander: false,      follow: false },
  drawer:   { dot: 'never',         glyph: false, badge: true,  animate: true,  wander: 'selected', follow: true },
};
export const ROLE_KEYS = Object.keys(ROLES) as Role[];
export const PHONE_MAX = 640;                       // px; phone width = viewport <= this
export const BUSY_STATES: AvatarState[] = ['working', 'needs_input'];
export const FOLLOW = { radius: 160 };               // px from the avatar centre
export const BADGE = { min: 28, tight: 24, gap: 1 }; // badge from 28 px; on hover below; never below `tight` next to a dot

export interface DotSpec { color: string; glyph: Glyph }
const roleOf = (role: string | undefined): RoleDef => ROLES[role as Role] || ROLES.sidebar;
const stateOf = (state: string | undefined): StateDef => STATES[state as AvatarState] || STATES.idle;

// Which dot (if any) a role shows: { color, glyph } or null.
export function dotFor({ role = 'sidebar', state = 'idle', size = 48, phone = false }: { role?: string; state?: string; size?: number; phone?: boolean } = {}): DotSpec | null {
  const R = roleOf(role), def = stateOf(state);
  if (R.dot === 'never' || !def.dot) return null;
  if (R.dot === 'phone-working') return phone && BUSY_STATES.includes(state as AvatarState) ? { color: def.dot, glyph: null } : null;
  return { color: def.dot, glyph: R.glyph && size >= 40 ? def.glyph : null };
}
// Eye-follow: selected agent only, sidebar + drawer only, within the radius,
// never while working (working glances win) and never asleep.
export function followAllowed({ role = 'sidebar', selected = false, state = 'idle', dist = Infinity, radius = FOLLOW.radius, asleep = false }: { role?: string; selected?: boolean; state?: string; dist?: number; radius?: number; asleep?: boolean } = {}): boolean {
  const R = roleOf(role);
  return !!(R.follow && selected && state !== 'working' && !asleep && dist <= radius);
}
export function wanderAllowed({ role = 'sidebar', selected = false }: { role?: string; selected?: boolean } = {}): boolean {
  return roleOf(role).wander === 'selected' && !!selected;
}
// Provider badge, bottom-left, mirrors the dot (same radius + cut-out rule).
// Returns pixel position relative to the body centre, or null when hidden.
export interface BadgePlace { x: number; y: number; r: number; cut: number; provider: string }
export function badgeFor({ role = 'sidebar', shape = 'pebble', size = 48, provider = null, hover = false, dot = null }: { role?: string; shape?: string; size?: number; provider?: string | null; hover?: boolean; dot?: { color: string } | null } = {}): BadgePlace | null {
  const R = roleOf(role);
  if (!provider || !R.badge) return null;
  if (size < BADGE.min && !hover) return null;
  const b = statusDot(shape, size, -1);
  if (dot) {
    if (size < BADGE.tight) return null; // tight: the status dot wins
    const d = statusDot(shape, size, 1), R2 = (d.r + d.cut) * DOT.pulse + (b.r + b.cut);
    if (Math.hypot(d.x - b.x, d.y - b.y) - R2 < BADGE.gap) return null;
  }
  return { x: b.x, y: b.y, r: b.r, cut: b.cut, provider };
}

// ------------------------------------------------------- shared agent state
// One live record per agent id: status, a one-line blurb (current step),
// provider and look. Every avatar instance of that agent reads the same
// record and the same AvatarModel, so the sidebar and the activity blurb
// animate on one clock (same breathing, blinks, glances, phase).
export interface AgentRecord {
  id: string; status: AvatarState; blurb: string; provider: string | null; since: number;
  name?: string; shape?: string; color?: string; eyeColor?: string; palette?: string;
  /** A saved look exists for this agent (server record), not just the hash default. */
  chosen?: boolean;
  /** When the blurb arrived (page clock, ms): a step line goes stale if nothing clears it. */
  blurbAt?: number;
}
export type AgentPatch = Partial<Omit<AgentRecord, 'id' | 'since'>>;
export interface AgentHub {
  get(id: string): AgentRecord | undefined;
  list(): AgentRecord[];
  set(id: string, patch: AgentPatch): AgentRecord;
  subscribe(id: string, fn: (a: AgentRecord | undefined) => void): () => void;
  subscribeAll(fn: (id: string, a: AgentRecord | undefined) => void): () => void;
  model(id: string, opts?: ModelOpts): AvatarModel;
}
export function createAgentState(): AgentHub {
  const agents = new Map<string, AgentRecord>(), subs = new Map<string, Set<(a: AgentRecord | undefined) => void>>();
  const all = new Set<(id: string, a: AgentRecord | undefined) => void>(), models = new Map<string, AvatarModel>();
  const emit = (id: string) => { const a = agents.get(id); (subs.get(id) || []).forEach((f) => f(a)); all.forEach((f) => f(id, a)); };
  const api: AgentHub = {
    get: (id) => agents.get(id),
    list: () => Array.from(agents.values()),
    set(id, patch) {
      const prev: AgentRecord = agents.get(id) || { id, status: 'idle', blurb: '', provider: null, since: Date.now() };
      const next: AgentRecord = { ...prev, ...patch, id };
      if (patch.status && patch.status !== prev.status) next.since = Date.now();
      agents.set(id, next);
      const m = models.get(id);
      if (m) {
        if (patch.status) m.setState(patch.status);
        const look: Record<string, string | undefined> = {};
        for (const k of ['shape', 'color', 'eyeColor', 'palette'] as const) if (k in patch) look[k] = patch[k];
        if (Object.keys(look).length) m.configure(look);
      }
      emit(id);
      return next;
    },
    subscribe(id, fn) {
      let s = subs.get(id);
      if (!s) { s = new Set(); subs.set(id, s); }
      s.add(fn);
      return () => { s.delete(fn); };
    },
    subscribeAll(fn) { all.add(fn); return () => { all.delete(fn); }; },
    // the one shared model per agent (created on first use)
    model(id, opts = {}) {
      let m = models.get(id);
      if (!m) {
        const a = agents.get(id) || api.set(id, {});
        m = new AvatarModel({ agentId: id, state: a.status, shape: a.shape, color: a.color, eyeColor: a.eyeColor, palette: a.palette, ...opts });
        models.set(id, m);
      }
      return m;
    },
  };
  return api;
}
export const agentState = createAgentState();
