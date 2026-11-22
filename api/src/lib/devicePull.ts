/**
 * Media from a connected device: a seat that runs on ANOTHER machine (registry.json agents[id].machine
 * names a machine this host reaches, lib/machineTarget.ts) writes its screenshots and recordings
 * THERE. A path in its reply means a file on that machine, never one here. Before the chat can
 * show it, the file is copied onto this host, into the seat's own folder under the data dir:
 *
 *     <data dir>/state/agents/<id>/device/<hash>-<size>-<mtime>-<name>
 *
 * which is one of the roots the media route already allows for that seat (lib/mediaStore.ts
 * agentMediaRoots), so the copy is served under exactly the same checks as any local file: one
 * open, no links, the type by its bytes, the size cap, the viewer's scope.
 *
 * WHO IS A DEVICE SEAT. The label is a label, not evidence (the liveness rule of
 * routes/agents-identity.ts resolveMachineAndLiveness, services/cross-machine.ts): a seat is read
 * over there only when its label names a machine this host reaches AND nothing on this host says it
 * runs here, i.e. no live tmux session of its own here and no transcript of its own here. Either one
 * makes it a seat of THIS host whatever the label says: its working folder stays a root, its paths
 * are its paths, and nothing is ever asked of the labelled machine. The evidence is answered by
 * routes/chat-transcript.ts (setLocalSeatEvidence, the same tmux and transcript resolution the chat
 * uses); until it is registered, a labelled seat counts as here (no evidence that it is elsewhere).
 *
 * THE PATH. The copy goes over the ONE cross-machine path this repo has: SSH to the machine the
 * registry names, with this host's key and the same options services/cross-machine.ts uses to
 * spawn, inject into and capture those seats. No new credential, no token in a URL, no agent on
 * the device. The remote side runs a fixed command on a path that passed a strict character
 * rule (single-quoted, no quote can occur): it refuses a path whose real path differs from itself
 * (a symlink anywhere), anything not a regular file, and then either prints the file's size and
 * mtime (the check) or sends at most the size the check saw + 1 byte (the copy).
 * Only a path under the seat's own working folder ON THAT MACHINE is asked for at all.
 *
 * WHEN. Stamping a transcript is synchronous and must never wait on a network. The first stamp
 * that sees a device path schedules the copy (bounded queue, two at a time, deduplicated) and
 * stamps nothing for it; a stamp after the copy landed finds it and mints an id as usual. A copy
 * that failed is not asked for again for a minute.
 *
 * REFRESH. A copy is named by the remote file's size and mtime. While a path is being stamped, the
 * device is asked again at most once a minute (REFRESH_MS, the size+mtime check only, no bytes);
 * a changed file is copied again under its new name, the old copy goes, and the next stamp mints a
 * new id for it (a new URL, so no browser shows the old frame from its cache).
 *
 * BOUND. Per seat, the device folder holds at most MAX_DEVICE_COPIES files and MAX_DEVICE_BYTES
 * bytes. Before a copy is taken, room is made by removing the copies least recently stamped; a
 * copy stamped in the last PROTECT_MS is never removed (the window on screen keeps its media), and
 * when the room cannot be made that way the new copy is not taken (only the size check crossed the
 * network). Leftover temp files of an interrupted copy go at the same time.
 */
import { spawn } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { closeSync, constants, existsSync, fstatSync, mkdirSync, openSync, readFileSync, readSync, readdirSync, renameSync, statSync, unlinkSync, writeSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { isThisHost, machineFrom, sshArgs, type MachineConfig } from './machineTarget.js';
import { MAX_VIDEO_BYTES, SNIFF_BYTES, maxBytesFor, sniffMedia } from './mediaSniff.js';

/** A seat on another machine this host reaches: where to ask, and the folder it may show from. */
export interface DeviceTarget { machineId: string; machine: MachineConfig; cwd: string }

/** What the device says about a file: its size in bytes and its mtime in whole seconds. */
export interface RemoteStat { size: number; mtime: number }

/** Copy `remotePath` from the device into `sink` (a new file, created by the reader), at most
 *  `maxBytes`. True when the whole file arrived. */
export type DeviceReader = (target: DeviceTarget, remotePath: string, maxBytes: number, sink: string) => Promise<boolean>;
/** The size and mtime of `remotePath` on the device (the same checks as a copy), or null. */
export type DeviceStatter = (target: DeviceTarget, remotePath: string) => Promise<RemoteStat | null>;

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
// The characters a device path may have: the detector's (lib/imageRefs.ts) and nothing else. No quote,
// no space, no `$`, no backtick: the path is single-quoted into the remote command and cannot close it.
const SAFE_REMOTE = /^\/[A-Za-z0-9._~+%@/-]+$/;
const PULL_TIMEOUT_MS = 60_000;
const STAT_TIMEOUT_MS = 15_000;
const FAIL_TTL_MS = 60_000;
/** A copy being stamped is checked against the device at most this often (ms). */
export const REFRESH_MS = 60_000;
/** A copy stamped this recently (ms) is never pruned. */
export const PROTECT_MS = 10 * 60_000;
/** Per seat: at most this many device copies... */
export const MAX_DEVICE_COPIES = 200;
/** ...and at most this many bytes of them (four videos at the video cap). */
export const MAX_DEVICE_BYTES = 4 * MAX_VIDEO_BYTES;
const STAT_LINE = /^(\d{1,15}) (\d{1,15})\s*$/;

function remoteChecks(remotePath: string): string {
  if (!SAFE_REMOTE.test(remotePath)) throw new Error('unsafe remote path');
  return `p='${remotePath}'; r=$(realpath "$p" 2>/dev/null) || exit 3; [ "$r" = "$p" ] || exit 4; [ -f "$p" ] || exit 5; `;
}

/** The remote side's command for one path: refuse a link or a non-file, send at most max+1 bytes. */
export function remoteReadCommand(remotePath: string, maxBytes: number): string {
  return remoteChecks(remotePath) + `exec head -c ${Math.floor(maxBytes) + 1} "$p"`;
}

/** The remote side's check for one path: the same refusals, then "<size> <mtime>" (GNU stat, else BSD/macOS stat). */
export function remoteStatCommand(remotePath: string): string {
  return remoteChecks(remotePath) + `stat -c '%s %Y' "$p" 2>/dev/null || exec stat -f '%z %m' "$p"`;
}

/** Is `path` a device path this seat may be asked for: absolute, safe characters, no traversal, under its cwd. */
export function deviceRemoteOk(path: string, cwd: string): boolean {
  if (!SAFE_REMOTE.test(path) || !SAFE_REMOTE.test(cwd) || cwd === '/') return false;
  if (path.includes('//') || /(^|\/)\.\.?(\/|$)/.test(path)) return false;
  const root = cwd.endsWith('/') ? cwd : cwd + '/';
  return path.startsWith(root) && path.length > root.length;
}

/** The seat's device folder here. */
export function deviceDir(dataDir: string, agentId: string): string {
  return join(resolve(dataDir), 'state', 'agents', agentId, 'device');
}

function copyHash(machineId: string, remotePath: string): string {
  return createHash('sha256').update(`${machineId}\0${remotePath}`).digest('hex').slice(0, 32);
}

/** Where the copy of one VERSION (size, mtime) of a device file lives here: the seat's own folder, a name no path can steer. */
export function deviceCopyPath(dataDir: string, agentId: string, machineId: string, remotePath: string, v: RemoteStat): string {
  const base = remotePath.slice(remotePath.lastIndexOf('/') + 1).replace(/[^A-Za-z0-9._-]/g, '_').slice(-80);
  return join(deviceDir(dataDir, agentId), `${copyHash(machineId, remotePath)}-${Math.floor(v.size)}-${Math.floor(v.mtime)}-${base}`);
}

/** Run one fixed command on the device over the cross-machine SSH path; its stdout goes to `onData`. */
function sshRun(target: DeviceTarget, command: string, timeoutMs: number, onData: (chunk: Buffer, kill: () => void) => void): Promise<number | null> {
  return new Promise((done) => {
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn('ssh', sshArgs(target.machine, command), { stdio: ['ignore', 'pipe', 'ignore'] });
    } catch { done(null); return; }
    let killed = false;
    const kill = () => { killed = true; child.kill('SIGKILL'); };
    const timer = setTimeout(kill, timeoutMs);
    timer.unref?.();
    child.stdout!.on('data', (chunk: Buffer) => { if (!killed) onData(chunk, kill); });
    child.on('error', () => { clearTimeout(timer); done(null); });
    child.on('close', (code) => { clearTimeout(timer); done(killed ? null : code); });
  });
}

/** The default reader: ssh to the machine (lib/machineTarget.ts sshArgs, this host's key), the bytes into `sink`. */
export const sshDeviceReader: DeviceReader = async (target, remotePath, maxBytes, sink) => {
  let fd: number;
  try { fd = openSync(sink, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600); } catch { return false; }
  let n = 0;
  let bad = false;
  try {
    const code = await sshRun(target, remoteReadCommand(remotePath, maxBytes), PULL_TIMEOUT_MS, (chunk, kill) => {
      if (bad) return;
      n += chunk.length;
      if (n > maxBytes) { bad = true; kill(); return; }
      try { writeSync(fd, chunk); } catch { bad = true; kill(); }
    });
    return !bad && code === 0 && n > 0;
  } finally { try { closeSync(fd); } catch { /* closed */ } }
};

/** The default check: the same SSH path, one short line back ("<size> <mtime>"), nothing else read. */
export const sshDeviceStat: DeviceStatter = async (target, remotePath) => {
  let out = '';
  const code = await sshRun(target, remoteStatCommand(remotePath), STAT_TIMEOUT_MS, (chunk, kill) => {
    out += chunk.toString('latin1');
    if (out.length > 64) kill();
  });
  const m = code === 0 ? STAT_LINE.exec(out) : null;
  return m ? { size: Number(m[1]), mtime: Number(m[2]) } : null;
};

/** Whether THIS host has evidence that a seat runs here (its own live tmux session, its own transcript). */
export type LocalSeatEvidence = (agentId: string, def: Record<string, unknown>) => boolean;
let localEvidence: LocalSeatEvidence | null = null;
/** Registered by routes/chat-transcript.ts (tmux + transcript resolution). Tests set their own; null clears it. */
export function setLocalSeatEvidence(fn: LocalSeatEvidence | null): void { localEvidence = fn; }

export interface LocatorOpts {
  /** Overrides the registered evidence (tests). */
  evidence?: LocalSeatEvidence;
  now?: () => number;
}

/**
 * The registry's view of one seat: on this host, or on a device this host reaches. Read at most every 2 s.
 * A device only with positive evidence: the label names a machine this host reaches AND this host has no
 * live session and no transcript of the seat (the label is a label, not evidence; see the header).
 */
export function registryLocator(dataDir: string, opts: LocatorOpts = {}): (agentId: string) => DeviceTarget | null {
  let cache: { at: number; reg: any; verdicts: Map<string, DeviceTarget | null> } | null = null;
  return (agentId) => {
    if (!SAFE_ID.test(agentId)) return null;
    const now = opts.now ? opts.now() : Date.now();
    if (!cache || now - cache.at > 2_000) {
      let reg: any = null;
      try { reg = JSON.parse(readFileSync(join(dataDir, 'registry.json'), 'utf-8')); } catch { reg = null; }
      cache = { at: now, reg, verdicts: new Map() };
    }
    if (cache.verdicts.has(agentId)) return cache.verdicts.get(agentId)!;
    const verdict = locate(cache.reg, agentId, opts.evidence ?? localEvidence);
    cache.verdicts.set(agentId, verdict);
    return verdict;
  };
}

function locate(reg: any, agentId: string, evidence: LocalSeatEvidence | null): DeviceTarget | null {
  const def = reg?.agents && Object.prototype.hasOwnProperty.call(reg.agents, agentId) ? reg.agents[agentId] : null;
  if (!def || isThisHost(def.machine)) return null;
  const machine = machineFrom(reg.machines || {}, String(def.machine), def);
  // A label naming a machine this host cannot reach is treated as here (a mislabelled local seat keeps
  // its images); only a reachable device seat has its paths read over there.
  if (!machine) return null;
  // No evidence source, or evidence that it runs HERE: a seat of this host, whatever its label.
  if (!evidence) return null;
  try { if (evidence(agentId, def)) return null; } catch { return null; }
  const cwd = typeof def.cwd === 'string' && def.cwd ? def.cwd : machine.home;
  return { machineId: String(def.machine), machine, cwd };
}

export interface DevicePullDeps {
  dataDir: string;
  locate: (agentId: string) => DeviceTarget | null;
  read?: DeviceReader;
  stat?: DeviceStatter;
  now?: () => number;
  concurrency?: number;
  maxQueue?: number;
  /** The bound per seat (tests may lower them). */
  maxCopies?: number;
  maxBytes?: number;
}

export class DevicePuller {
  private inflight = new Set<string>();
  private failed = new Map<string, number>();
  private queue: Array<() => Promise<void>> = [];
  private running = 0;
  private waiters: Array<() => void> = [];
  /** Per seat device folder: copy hash -> its current file name (built from the folder once, then kept). */
  private index = new Map<string, Map<string, string>>();
  /** A copy's full path -> when a stamp last showed it (ms). */
  private used = new Map<string, number>();
  /** (agent, machine, path) -> when the device was last asked about it (ms). */
  private checked = new Map<string, number>();
  /** Bytes being copied into a seat's folder right now, so two copies do not both spend the same room. */
  private reserved = new Map<string, { bytes: number; count: number }>();
  private temps = new Set<string>();

  constructor(private readonly deps: DevicePullDeps) {}

  private now(): number { return this.deps.now ? this.deps.now() : Date.now(); }

  /** Is this seat on another machine (its paths are read there)? */
  isDevice(agentId: string): boolean { return !!this.deps.locate(agentId); }

  private dirIndex(dir: string): Map<string, string> {
    let m = this.index.get(dir);
    if (m) return m;
    m = new Map();
    try {
      for (const f of readdirSync(dir)) {
        const hit = /^([0-9a-f]{32})-\d+-\d+-/.exec(f);
        if (hit) m.set(hit[1], f);
      }
    } catch { /* no folder yet */ }
    this.index.set(dir, m);
    return m;
  }

  /**
   * The file a stamp should look at for the path a seat wrote: the path itself for a seat on this
   * host; for a device seat, the local copy once it is here (else null, and the copy is scheduled).
   * A device path outside the seat's working folder over there is null and never asked for. A copy
   * shown here is re-checked against the device at most every REFRESH_MS.
   */
  served(agentId: string, path: string): string | null {
    const target = this.deps.locate(agentId);
    if (!target) return path;
    if (!SAFE_ID.test(agentId) || !deviceRemoteOk(path, target.cwd)) return null;
    const dir = deviceDir(this.deps.dataDir, agentId);
    const hash = copyHash(target.machineId, path);
    const idx = this.dirIndex(dir);
    const name = idx.get(hash);
    const now = this.now();
    const key = `${agentId}\0${target.machineId}\0${path}`;
    if (name) {
      const local = join(dir, name);
      if (existsSync(local)) {
        this.used.set(local, now);
        const at = this.checked.get(key);
        if (at === undefined) this.checked.set(key, now);   // first sight since start: its name is its version
        else if (now - at >= REFRESH_MS) this.schedule(agentId, target, path, key);
        return local;
      }
      idx.delete(hash);
    }
    this.schedule(agentId, target, path, key);
    return null;
  }

  private schedule(agentId: string, target: DeviceTarget, path: string, key: string): void {
    const now = this.now();
    const until = this.failed.get(key);
    if (until !== undefined) { if (until > now) return; this.failed.delete(key); }
    if (this.inflight.has(key) || this.queue.length >= (this.deps.maxQueue ?? 64)) return;
    this.inflight.add(key);
    this.queue.push(async () => {
      const ok = await this.pull(agentId, target, path, key).catch(() => false);
      this.inflight.delete(key);
      if (!ok) {
        this.failed.set(key, this.now() + FAIL_TTL_MS);
        if (this.failed.size > 4096) for (const k of this.failed.keys()) { this.failed.delete(k); if (this.failed.size <= 2048) break; }
      }
      if (this.checked.size > 8192) for (const k of this.checked.keys()) { this.checked.delete(k); if (this.checked.size <= 4096) break; }
    });
    this.pump();
  }

  private pump(): void {
    while (this.running < (this.deps.concurrency ?? 2) && this.queue.length) {
      const job = this.queue.shift()!;
      this.running++;
      void job().finally(() => {
        this.running--;
        this.pump();
        if (!this.running && !this.queue.length) for (const w of this.waiters.splice(0)) w();
      });
    }
  }

  /**
   * Bring one device file here, or confirm the copy here is current: ask its size and mtime; the same
   * version already here is done. Otherwise make room (prune), copy into a fresh temp name in the seat's
   * folder, check it (exactly the size the device said, an image or a video by its bytes, under that
   * kind's cap), rename it into place under its version's name, and drop the previous version.
   * Anything else leaves nothing behind (and an older copy, if any, stays served).
   */
  private async pull(agentId: string, target: DeviceTarget, path: string, key: string): Promise<boolean> {
    const dir = deviceDir(this.deps.dataDir, agentId);
    const hash = copyHash(target.machineId, path);
    const st = await (this.deps.stat ?? sshDeviceStat)(target, path);
    this.checked.set(key, this.now());
    if (!st || st.size <= 0 || st.size > MAX_VIDEO_BYTES) return false;
    const local = deviceCopyPath(this.deps.dataDir, agentId, target.machineId, path, st);
    const idx = this.dirIndex(dir);
    const prev = idx.get(hash);
    if (prev && join(dir, prev) === local && existsSync(local)) return true;
    try { mkdirSync(dir, { recursive: true, mode: 0o700 }); } catch { return false; }
    if (!this.makeRoom(dir, st.size, prev ? join(dir, prev) : null)) return false;
    this.reserve(dir, st.size, 1);
    const tmp = join(dir, `.pull-${randomBytes(8).toString('hex')}.part`);
    this.temps.add(tmp);
    const read = this.deps.read ?? sshDeviceReader;
    let ok = false;
    try {
      ok = await read(target, path, st.size, tmp);
      if (ok) ok = checkCopy(tmp, st.size);
      if (ok) {
        renameSync(tmp, local);
        idx.set(hash, local.slice(dir.length + 1));
        this.used.set(local, this.now());
        if (prev && join(dir, prev) !== local) this.remove(join(dir, prev));
      }
      return ok;
    } catch { return false; } finally {
      this.temps.delete(tmp);
      this.reserve(dir, -st.size, -1);
      if (!ok) { try { unlinkSync(tmp); } catch { /* never created */ } }
    }
  }

  private reserve(dir: string, bytes: number, count: number): void {
    const r = this.reserved.get(dir) ?? { bytes: 0, count: 0 };
    r.bytes = Math.max(0, r.bytes + bytes);
    r.count = Math.max(0, r.count + count);
    if (r.count === 0) this.reserved.delete(dir); else this.reserved.set(dir, r);
  }

  private remove(file: string): void {
    try { unlinkSync(file); } catch { /* gone */ }
    this.used.delete(file);
  }

  /**
   * Room in a seat's device folder for one more copy of `incoming` bytes, within MAX_DEVICE_COPIES and
   * MAX_DEVICE_BYTES: the copies stamped least recently go first (never one stamped within PROTECT_MS),
   * along with leftover temp files. `replacing` (the previous version of the same file, dropped once
   * the new one lands) is not counted. False when the room cannot be made.
   */
  private makeRoom(dir: string, incoming: number, replacing: string | null): boolean {
    const maxCopies = this.deps.maxCopies ?? MAX_DEVICE_COPIES;
    const maxBytes = this.deps.maxBytes ?? MAX_DEVICE_BYTES;
    if (incoming > maxBytes) return false;
    const now = this.now();
    const idx = this.dirIndex(dir);
    const files: Array<{ file: string; size: number; at: number }> = [];
    const held = this.reserved.get(dir) ?? { bytes: 0, count: 0 };
    let bytes = held.bytes;
    let names: string[] = [];
    try { names = readdirSync(dir); } catch { names = []; }
    for (const f of names) {
      const file = join(dir, f);
      let st;
      try { st = statSync(file); } catch { continue; }
      if (f.endsWith('.part')) {
        if (!this.temps.has(file) && now - st.mtimeMs > 2 * PULL_TIMEOUT_MS) this.remove(file);
        continue;
      }
      if (file === replacing) continue;
      files.push({ file, size: st.size, at: this.used.get(file) ?? st.mtimeMs });
      bytes += st.size;
    }
    files.sort((a, b) => a.at - b.at);
    let count = files.length + held.count;
    for (const f of files) {
      if (count + 1 <= maxCopies && bytes + incoming <= maxBytes) break;
      if (now - f.at < PROTECT_MS) return false;
      this.remove(f.file);
      const name = f.file.slice(dir.length + 1);
      const hit = /^([0-9a-f]{32})-/.exec(name);
      if (hit && idx.get(hit[1]) === name) idx.delete(hit[1]);
      count--;
      bytes -= f.size;
    }
    return count + 1 <= maxCopies && bytes + incoming <= maxBytes;
  }

  /** Tests: resolves once nothing is queued or running. */
  idle(): Promise<void> {
    if (!this.running && !this.queue.length) return Promise.resolve();
    return new Promise((r) => this.waiters.push(r));
  }
}

function checkCopy(file: string, expected: number): boolean {
  let fd: number;
  try { fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW); } catch { return false; }
  try {
    const st = fstatSync(fd);
    if (!st.isFile() || st.size === 0 || st.size !== expected) return false;
    const head = Buffer.alloc(SNIFF_BYTES);
    const got = readSync(fd, head, 0, SNIFF_BYTES, 0);
    const media = sniffMedia(head.subarray(0, got));
    return !!media && st.size <= maxBytesFor(media.kind);
  } catch { return false; } finally { try { closeSync(fd); } catch { /* closed */ } }
}

const pullers = new Map<string, DevicePuller>();
/** The install's puller (one per data dir), reading seats' machines from its registry. */
export function devicePuller(dataDir: string): DevicePuller {
  const d = resolve(dataDir);
  let p = pullers.get(d);
  if (!p) { p = new DevicePuller({ dataDir: d, locate: registryLocator(d) }); pullers.set(d, p); }
  return p;
}
