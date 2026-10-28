/*
 * Living Avatars canvas renderer + shared ticker (web only), ported from the prototype's avatar-canvas.js.
 *
 * drawAvatar() is the only drawing code. It reads a Pose from avatarCore.ts and uses plain 2D path
 * operations (moveTo, quadraticCurveTo, arc, rect clip, solid fills and strokes) that map one to one onto
 * SwiftUI Path / GraphicsContext. Flat style: no gradients, shadows or highlights, and no mouth.
 *
 * The ticker runs one requestAnimationFrame loop for every avatar on the page, skips avatars that are off
 * screen (IntersectionObserver), and skips still avatars (reduced motion, the pill) until something about
 * them changes. A shared model (one per agent, from the hub) steps once per frame however many instances
 * show it, so every animated instance of an agent is on the same clock.
 */
import {
  shapeOutline, faceLayout, SHAPES, LAYOUT, statusDot, paint, dotFor, badgeFor, followAllowed, wanderAllowed,
  STATES, FOLLOW, PHONE_MAX,
  type AvatarModel, type Pose, type Theme, type Glyph, type DotSpec, type BadgePlace, type Pt, type Role,
} from './avatarCore';
import { brandUrl, type ProviderId } from './brandMarks';

const EYE = '#231F2E';
// Cut-out ring behind the dot and the badge. It must match the surface the avatar sits on, so it reads as a
// real cut-out in both themes: each canvas reads the CSS custom property --la-surface (inherited from its
// container), falling back to white (light) / #0A0A0A (dark).
export const SURFACE: Record<Theme, string> = { light: '#FFFFFF', dark: '#0A0A0A' };

// ------------------------------------------------------------ theme
const themeNow = (): Theme => (typeof document !== 'undefined' && document.documentElement.classList.contains('dark') ? 'dark' : 'light');
let theme: Theme = themeNow(), themeVersion = 0;
if (typeof MutationObserver !== 'undefined' && typeof document !== 'undefined') {
  new MutationObserver(() => { const t = themeNow(); if (t !== theme) { theme = t; themeVersion++; } })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
}
export const currentTheme = (): Theme => theme;

// ------------------------------------------------------- brand marks
const brandImgs = new Map<string, HTMLImageElement>();
let brandVersion = 0;
function brandImage(provider: string, th: Theme): HTMLImageElement | null {
  if (typeof Image === 'undefined') return null;
  let src: string;
  try { src = brandUrl(provider as ProviderId, th); } catch { return null; }
  if (!src) return null;
  let img = brandImgs.get(src);
  if (!img) {
    img = new Image();
    img.onload = () => { brandVersion++; };
    img.src = src;
    brandImgs.set(src, img);
  }
  return img.complete && img.naturalWidth ? img : null;
}

function bodyPath(ctx: CanvasRenderingContext2D, pts: Pt[], u: number) {
  const n = pts.length;
  const mid = (i: number): Pt => {
    const a = pts[i % n], b = pts[(i + 1) % n];
    return [((a[0] + b[0]) / 2) * u, ((a[1] + b[1]) / 2) * u];
  };
  ctx.beginPath();
  let m = mid(n - 1);
  ctx.moveTo(m[0], m[1]);
  for (let i = 0; i < n; i++) {
    m = mid(i);
    ctx.quadraticCurveTo(pts[i][0] * u, pts[i][1] * u, m[0], m[1]);
  }
  ctx.closePath();
}

// Status dot: surface-coloured cut-out disc, then the state colour, then a white glyph.
function drawStatusDot(ctx: CanvasRenderingContext2D, glyph: Glyph, color: string, x: number, y: number, r: number, cut: number, detailed: boolean, phase: number, surface = SURFACE.light) {
  ctx.beginPath();
  ctx.arc(x, y, r + cut, 0, Math.PI * 2);
  ctx.fillStyle = surface;
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  if (!detailed || !glyph) return;
  ctx.strokeStyle = '#fff';
  ctx.fillStyle = '#fff';
  ctx.lineWidth = r * 0.26;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  if (glyph === 'dots') {
    // three dots with a gentle wave while working (still when phase is 0)
    for (let i = -1; i <= 1; i++) {
      const lift = phase ? Math.max(0, Math.sin(phase * 7 - (i + 1) * 0.9)) * r * 0.16 : 0;
      ctx.beginPath(); ctx.arc(x + i * r * 0.46, y - lift, r * 0.15, 0, Math.PI * 2); ctx.fill();
    }
  } else if (glyph === 'check') {
    ctx.moveTo(x - r * 0.42, y + r * 0.02);
    ctx.lineTo(x - r * 0.1, y + r * 0.34);
    ctx.lineTo(x + r * 0.44, y - r * 0.3);
    ctx.stroke();
  } else if (glyph === 'alert') {
    ctx.moveTo(x, y - r * 0.48); ctx.lineTo(x, y + r * 0.08); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y + r * 0.42, r * 0.14, 0, Math.PI * 2); ctx.fill();
  } else if (glyph === 'question') {
    ctx.arc(x, y - r * 0.2, r * 0.28, Math.PI * 1.1, Math.PI * 2.45);
    ctx.lineTo(x, y + r * 0.14);
    ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y + r * 0.46, r * 0.13, 0, Math.PI * 2); ctx.fill();
  }
}

// Provider badge: the official mark itself at full badge size, with no plate behind it (no recolouring).
// A thin halo in the surface colour, shaped like the mark rather than a disc, keeps it legible where it
// overlaps the body.
function drawBadge(ctx: CanvasRenderingContext2D, provider: string, x: number, y: number, r: number, cut: number, th: Theme, surface: string) {
  const img = brandImage(provider, th);
  if (!img) return;
  const s = r * 2.2, iw = img.naturalWidth || 1, ih = img.naturalHeight || 1, k = s / Math.max(iw, ih);
  const w = iw * k, h = ih * k, x0 = x - w / 2, y0 = y - h / 2;
  const halo = Math.max(1, cut * 0.8);
  ctx.save();
  if ('filter' in ctx) {
    ctx.filter = `drop-shadow(0 0 ${halo * 0.5}px ${surface}) drop-shadow(0 0 ${halo * 0.5}px ${surface}) drop-shadow(0 0 ${halo * 0.5}px ${surface})`;
  }
  ctx.drawImage(img, x0, y0, w, h);
  ctx.restore();
  ctx.drawImage(img, x0, y0, w, h);
}

// One eye: a solid disc cut by a top and a bottom lid (lid model from bbot, MIT).
// sg is -1 for the left eye, +1 for the right; +tilt drops the inner edge.
function drawEye(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, lidT: number, lidB: number, tilt: number, sg: number, lineW: number) {
  if (lidT + lidB >= 0.9) {
    // shut: one short flat stroke with a slight downward curve
    ctx.save(); ctx.translate(x, y + r * 0.1); ctx.rotate(-sg * tilt * 0.6);
    ctx.lineWidth = lineW; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(-r * 0.85, 0); ctx.quadraticCurveTo(0, r * 0.45, r * 0.85, 0); ctx.stroke();
    ctx.restore();
    return;
  }
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(-sg * tilt);
  const R = r * 1.06;
  const top = -R + 2 * R * lidT, bot = R - 2 * R * lidB;
  ctx.beginPath(); ctx.rect(-r * 2, top, r * 4, Math.max(0, bot - top)); ctx.clip();
  ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

export interface DrawOpts {
  theme?: Theme;
  surface?: string;
  /** undefined -> the pose's own dot (sidebar rules); null -> none; { color, glyph } -> that dot (dotFor) */
  dot?: DotSpec | null;
  /** null / a placement from badgeFor */
  badge?: BadgePlace | null;
}

// size in CSS px; ctx already scaled for devicePixelRatio. Flat style: solid fills only, no gradients,
// shadows, highlights or outlines. No mouth.
export function drawAvatar(ctx: CanvasRenderingContext2D, size: number, pose: Pose, opts: DrawOpts = {}): void {
  const s = SHAPES[pose.shape] || SHAPES.pebble;
  const th = opts.theme || theme;
  const surface = opts.surface || SURFACE[th];
  const ink = paint(pose, th);
  const m = pose.mood;
  const u = size * LAYOUT.u;
  const cx = size / 2, cy = size * LAYOUT.cy;
  ctx.clearRect(0, 0, size, size);

  ctx.save();
  // rotate about the body centre (so a spin stays in the box);
  // squash/stretch anchored at the feet so breathing doesn't float
  ctx.translate(cx + pose.dx * u, cy + pose.dy * u);
  ctx.rotate(pose.rot);
  ctx.translate(0, s.h * u);
  ctx.scale(pose.sx, pose.sy);
  ctx.translate(0, -s.h * u);

  ctx.fillStyle = ink.body;
  ctx.strokeStyle = ink.body;

  // antenna, same flat colour as the body
  if (s.antenna) {
    const top = -s.h * u * 0.98, sway = Math.sin(pose.dx * 20 + pose.lookX) * 0.12 * u;
    ctx.lineWidth = Math.max(1.2, u * 0.08);
    ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(0, top + u * 0.1); ctx.quadraticCurveTo(u * 0.02, top - u * 0.18, sway + u * 0.08, top - u * 0.34); ctx.stroke();
    ctx.beginPath(); ctx.arc(sway + u * 0.08, top - u * 0.36, u * 0.1, 0, Math.PI * 2); ctx.fill();
  }

  // flat body
  bodyPath(ctx, shapeOutline(pose.shape), u);
  ctx.fill();

  // face: two solid eyes, nothing else (placement from faceLayout, shared with Swift)
  const f = faceLayout(pose.shape, m, pose.lookX, pose.lookY);
  ctx.save();
  ctx.translate(0, f.fy * u);
  ctx.rotate(f.rot);
  // blink closes both lids toward the middle
  const k = 1 - Math.max(0, Math.min(1, pose.lid));
  const lidT = m.lidT + (0.5 - m.lidT) * k, lidB = m.lidB + (0.5 - m.lidB) * k;
  ctx.fillStyle = ink.eye || EYE;
  ctx.strokeStyle = ink.eye || EYE;
  const er0 = f.eyes[0][2] * u;
  const lineW = Math.max(1.2, er0 * 0.42);
  // a wink closes the second eye only, on top of any blink
  const kw = Math.max(k, Math.max(0, Math.min(1, pose.wink || 0)));
  const wT = m.lidT + (0.5 - m.lidT) * kw, wB = m.lidB + (0.5 - m.lidB) * kw;
  f.eyes.forEach(([x, y, r], i) => drawEye(ctx, x * u, y * u, r * u, i ? wT : lidT, i ? wB : lidB, m.tilt, i ? 1 : -1, lineW));
  ctx.restore();

  ctx.restore();

  // status dot on the bottom-right edge of the body. It follows the body's hops and shakes but not its
  // rotation or squash, so it stays readable.
  const dot = opts.dot === undefined ? (pose.dot ? { color: pose.dot, glyph: size >= 40 ? pose.glyph : null } : null) : opts.dot;
  if (opts.badge) {
    const b = opts.badge;
    drawBadge(ctx, b.provider, cx + pose.dx * u + b.x, cy + pose.dy * u + b.y, b.r, b.cut, th, surface);
  }
  if (dot) {
    const d = statusDot(pose.shape, size);
    const kk = pose.pulse != null ? 1 + 0.12 * Math.sin(Math.PI * pose.pulse) : 1;
    drawStatusDot(ctx, dot.glyph, dot.color, cx + pose.dx * u + d.x, cy + pose.dy * u + d.y, d.r * kk, d.cut * kk, !!dot.glyph, pose.phase, surface);
  }

  // zzz: same ink as the eyes. White z's sit on the page, so they get a body-coloured edge to stay visible
  // on light backgrounds.
  if (pose.zzz != null && size >= 44) {
    const zi = ink.eye || EYE, light = zi === '#FFFFFF';
    ctx.fillStyle = zi;
    ctx.strokeStyle = ink.body;
    ctx.lineJoin = 'round';
    ctx.lineWidth = Math.max(1.2, size * 0.016);
    ctx.textAlign = 'center';
    for (let i = 0; i < 2; i++) {
      const p = (pose.zzz + i * 0.5) % 1;
      ctx.globalAlpha = Math.sin(Math.PI * p) * (light ? 1 : 0.75);
      ctx.font = `800 ${Math.round(size * (0.1 + 0.06 * p))}px system-ui, sans-serif`;
      const zx = size * (0.78 + 0.1 * p), zy = size * (0.22 - 0.14 * p);
      if (light) ctx.strokeText('z', zx, zy);
      ctx.fillText('z', zx, zy);
    }
    ctx.globalAlpha = 1;
  }
}

// ---------------------------------------------------------------- ticker
// One rAF loop for every avatar. Per instance we add only what depends on where it sits: eye-follow,
// wander gating, dot, badge.
export interface AttachOpts {
  role?: Role; selected?: boolean; provider?: string | null; followRadius?: number; legacyDot?: boolean;
  /** Follow a mouse from any distance and keep looking at it while it rests (not only within the radius and
   *  for a few seconds after it moves). Touch keeps the usual rules, so a face never stares at an old tap. */
  followAlways?: boolean;
}
interface Shown { dot: DotSpec | { color: string } | null; badge: BadgePlace | null; following: boolean; look: { x: number; y: number }; t: number; blinkAt: number }
interface Entry {
  canvas: HTMLCanvasElement; model: AvatarModel; size: number; dpr: number; ctx: CanvasRenderingContext2D;
  visible: boolean; drawnKey: string | null; opts: AttachOpts & { role: Role }; look: { x: number; y: number };
  hover: boolean; surface?: string; surfaceTheme: number; following?: boolean; shown?: Shown;
}
type LaCanvas = HTMLCanvasElement & { __la?: Entry };

const entries = new Set<Entry>();
const pointer = { x: 0, y: 0, at: -1e9, inside: false, mouse: false };
export const stats = { fps: 0, count: 0 };
let raf = 0, last = 0, fpsAcc = 0, fpsFrames = 0, frameNo = 0;

if (typeof window !== 'undefined') {
  const mark = (e: PointerEvent) => { pointer.x = e.clientX; pointer.y = e.clientY; pointer.at = performance.now(); pointer.inside = true; pointer.mouse = e.pointerType === 'mouse'; };
  window.addEventListener('pointermove', mark, { passive: true });
  window.addEventListener('pointerdown', mark, { passive: true });
  document.addEventListener('pointerleave', () => { pointer.inside = false; });
  // the cursor left the window (no element under it next): nothing to look at
  window.addEventListener('mouseout', (e) => { if (!e.relatedTarget) pointer.inside = false; }, { passive: true });
}

const io = typeof IntersectionObserver !== 'undefined'
  ? new IntersectionObserver((list) => { for (const it of list) { const e = (it.target as LaCanvas).__la; if (e) e.visible = it.isIntersecting; } })
  : null;

const isPhone = () => typeof innerWidth !== 'undefined' && innerWidth <= PHONE_MAX;

function surfaceOf(e: Entry): string {
  if (e.surfaceTheme !== themeVersion) {
    const v = getComputedStyle(e.canvas).getPropertyValue('--la-surface').trim();
    e.surface = v || SURFACE[theme];
    e.surfaceTheme = themeVersion;
  }
  return e.surface as string;
}

function sharedPose(model: AvatarModel, dt: number): Pose {
  if (model.__frame !== frameNo) { model.__frame = frameNo; model.__pose = model.step(dt, null); }
  return { ...(model.__pose as Pose) };
}

function frame(now: number) {
  raf = requestAnimationFrame(frame);
  frameNo++;
  const dt = Math.min(0.05, (now - last) / 1000 || 0.016);
  last = now;
  fpsAcc += dt; fpsFrames++;
  if (fpsAcc >= 0.5) { stats.fps = Math.round(fpsFrames / fpsAcc); fpsAcc = 0; fpsFrames = 0; }
  const recent = pointer.inside && now - pointer.at < 4000;
  const phone = isPhone();
  let drawn = 0;
  for (const e of entries) {
    if (!e.visible) continue;
    const model = e.model, o = e.opts;
    // the pill is a still picture: no wander or other motion
    const still = model.reducedMotion || o.role === 'pill';
    // only the selected agent is awake while idle: the others doze (still, under reduced motion)
    const dozing = model.reducedMotion && o.role === 'sidebar' && !o.selected && model.sleepAfterMs > 0;
    if (!model.__seen) { model.__seen = true; if (!o.selected) model.settleAsleepSoon(); }
    const key = still ? [model.version, themeVersion, brandVersion, phone, o.selected, o.provider, e.hover, o.role, dozing].join('|') : null;
    if (still && e.drawnKey === key) continue;

    const pose = still ? model.stillPose(dozing) : sharedPose(model, dt);
    // eye-follow: proximity, selected sidebar / drawer only, never while working
    if (!still) {
      const r = e.canvas.getBoundingClientRect();
      const px = pointer.x - (r.left + r.width / 2), py = pointer.y - (r.top + r.height / 2), dist = Math.hypot(px, py);
      const radius = o.followRadius ?? FOLLOW.radius;
      // An idle avatar falls asleep after sleepAfterMs, and an asleep one neither wanders nor follows, so on a
      // page left open the selected avatar would go still for good: the selected one is kept awake instead.
      if (o.selected) model.notice();
      // a glance it was asked for, or a hello still playing, wins over the cursor
      const glancing = model.t < model.glanceUntil || model.t < model.followAfter;
      const always = !!o.followAlways && pointer.inside && pointer.mouse;
      const follow = (always || recent) && !glancing
        && followAllowed({ role: o.role, selected: o.selected, state: model.state, dist: always ? 0 : dist, radius, asleep: model.asleep });
      // idle wander only on the selected sidebar avatar; elsewhere idle eyes rest at centre
      const wander = wanderAllowed({ role: o.role, selected: o.selected }) || STATES[model.state].gaze !== 'free' || model.asleep;
      let tx = wander ? pose.lookX : 0, ty = wander ? pose.lookY : 0;
      if (follow) {
        // far away the eyes go fully to that side; close in they ease toward centre (the usual radius sets how close)
        const mag = Math.min(1, dist / ((always ? FOLLOW.radius : radius) * 0.5)), d = dist || 1;
        tx = (px / d) * mag; ty = (py / d) * mag;
      }
      const k = 1 - Math.exp(-dt * (follow ? 12 : 6)); // ease back to centre a little slower
      e.look.x += (tx - e.look.x) * k; e.look.y += (ty - e.look.y) * k;
      pose.lookX = e.look.x; pose.lookY = e.look.y;
      e.following = follow;
    }
    const dot = o.legacyDot ? undefined : dotFor({ role: o.role, state: model.state, size: e.size, phone });
    const shownDot = dot === undefined ? (pose.dot ? { color: pose.dot } : null) : dot;
    const badge = badgeFor({ role: o.role, shape: pose.shape, size: e.size, provider: o.provider, hover: e.hover, dot: shownDot });
    e.ctx.setTransform(e.dpr, 0, 0, e.dpr, 0, 0);
    drawAvatar(e.ctx, e.size, pose, { theme, surface: surfaceOf(e), dot, badge });
    e.shown = { dot: shownDot, badge, following: !!e.following, look: { x: pose.lookX, y: pose.lookY }, t: model.t, blinkAt: model.blinkAt };
    if (still) e.drawnKey = key;
    drawn++;
  }
  stats.count = drawn;
}

/** Draw `model` on `canvas` every frame (or once, when still). Returns detach(), with detach.update(opts). */
export function attach(canvas: HTMLCanvasElement, model: AvatarModel, size: number, opts: AttachOpts = {}): (() => void) & { update: (patch: AttachOpts) => void } {
  const dpr = Math.min(2, (typeof window !== 'undefined' && window.devicePixelRatio) || 1);
  canvas.width = Math.round(size * dpr);
  canvas.height = Math.round(size * dpr);
  const ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
  const e: Entry = { canvas, model, size, dpr, ctx, visible: !io, drawnKey: null, opts: { ...opts, role: opts.role ?? 'sidebar' }, look: { x: 0, y: 0 }, hover: false, surfaceTheme: -1 };
  const enter = () => { e.hover = true; }, leave = () => { e.hover = false; };
  canvas.addEventListener('pointerenter', enter);
  canvas.addEventListener('pointerleave', leave);
  (canvas as LaCanvas).__la = e;
  entries.add(e);
  if (io) io.observe(canvas);
  if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
  const detach = (() => {
    entries.delete(e);
    canvas.removeEventListener('pointerenter', enter);
    canvas.removeEventListener('pointerleave', leave);
    if (io) io.unobserve(canvas);
    delete (canvas as LaCanvas).__la;
    if (!entries.size && raf) { cancelAnimationFrame(raf); raf = 0; }
  }) as (() => void) & { update: (patch: AttachOpts) => void };
  // A changed role, selection or provider can change the surface behind the avatar too: read it again.
  detach.update = (patch) => { Object.assign(e.opts, patch); e.drawnKey = null; e.surfaceTheme = -1; };
  return detach;
}

/** Debug / browser checks: what a canvas showed on its last frame. */
export function inspect(canvas: HTMLCanvasElement): Shown | null {
  return (canvas as LaCanvas).__la?.shown ?? null;
}
