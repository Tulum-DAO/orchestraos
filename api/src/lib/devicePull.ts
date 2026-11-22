/**
 * Media from a connected device: a seat that runs on ANOTHER machine (registry.json agents[id].machine
 * names a machine this host reaches, lib/machineTarget.ts) writes its screenshots and recordings
 * THERE. A path in its reply means a file on that machine, never one here. Before the chat can
 * show it, the file is copied onto this host, into the seat's own folder under the data dir:
 *
 *     <data dir>/state/agents/<id>/device/<hash>-<name>
 *
 * which is one of the roots the media route already allows for that seat (lib/mediaStore.ts
 * agentMediaRoots), so the copy is served under exactly the same checks as any local file: one
 * open, no links, the type by its bytes, the size cap, the viewer's scope.
 *
 * THE PATH. The copy goes over the ONE cross-machine path this repo has: SSH to the machine the
 * registry names, with this host's key and the same options services/cross-machine.ts uses to
 * spawn, inject into and capture those seats. No new credential, no token in a URL, no agent on
 * the device. The remote side runs a fixed command on a path that passed a strict character
 * rule (single-quoted, no quote can occur): it refuses a path whose real path differs from itself
 * (a symlink anywhere), anything not a regular file, and it sends at most the video cap + 1 byte.
 * Only a path under the seat's own working folder ON THAT MACHINE is asked for at all.
 *
 * WHEN. Stamping a transcript is synchronous and must never wait on a network. The first stamp
 * that sees a device path schedules the copy (bounded queue, two at a time, deduplicated) and
 * stamps nothing for it; a stamp after the copy landed finds it and mints an id as usual. A copy
 * that failed is not asked for again for a minute. A copy is taken once per (machine, path): a
 * file the seat later overwrites on the device keeps the first copy here.
 */
import { spawn } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { closeSync, constants, existsSync, fstatSync, mkdirSync, openSync, readFileSync, readSync, renameSync, unlinkSync, writeSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { isThisHost, machineFrom, sshArgs, type MachineConfig } from './machineTarget.js';
import { MAX_VIDEO_BYTES, SNIFF_BYTES, maxBytesFor, sniffMedia } from './mediaSniff.js';

/** A seat on another machine this host reaches: where to ask, and the folder it may show from. */
export interface DeviceTarget { machineId: string; machine: MachineConfig; cwd: string }

/** Copy `remotePath` from the device into `sink` (a new file, created by the reader), at most
 *  `maxBytes`. True when the whole file arrived. */
export type DeviceReader = (target: DeviceTarget, remotePath: string, maxBytes: number, sink: string) => Promise<boolean>;

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
// The characters a device path may have: the detector's (lib/imageRefs.ts) and nothing else. No quote,
// no space, no `$`, no backtick: the path is single-quoted into the remote command and cannot close it.
const SAFE_REMOTE = /^\/[A-Za-z0-9._~+%@/-]+$/;
const PULL_TIMEOUT_MS = 60_000;
const FAIL_TTL_MS = 60_000;

/** The remote side's command for one path: refuse a link or a non-file, send at most max+1 bytes. */
export function remoteReadCommand(remotePath: string, maxBytes: number): string {
  if (!SAFE_REMOTE.test(remotePath)) throw new Error('unsafe remote path');
  return `p='${remotePath}'; r=$(realpath "$p" 2>/dev/null) || exit 3; [ "$r" = "$p" ] || exit 4; `
    + `[ -f "$p" ] || exit 5; exec head -c ${Math.floor(maxBytes) + 1} "$p"`;
}

/** Is `path` a device path this seat may be asked for: absolute, safe characters, no traversal, under its cwd. */
export function deviceRemoteOk(path: string, cwd: string): boolean {
  if (!SAFE_REMOTE.test(path) || !SAFE_REMOTE.test(cwd) || cwd === '/') return false;
  if (path.includes('//') || /(^|\/)\.\.?(\/|$)/.test(path)) return false;
  const root = cwd.endsWith('/') ? cwd : cwd + '/';
  return path.startsWith(root) && path.length > root.length;
}

/** Where the copy of a device file lives here: the seat's own folder, a name no path can steer. */
export function deviceCopyPath(dataDir: string, agentId: string, machineId: string, remotePath: string): string {
  const hash = createHash('sha256').update(`${machineId}\0${remotePath}`).digest('hex').slice(0, 32);
  const base = remotePath.slice(remotePath.lastIndexOf('/') + 1).replace(/[^A-Za-z0-9._-]/g, '_').slice(-80);
  return join(resolve(dataDir), 'state', 'agents', agentId, 'device', `${hash}-${base}`);
}

/** The default reader: ssh to the machine (lib/machineTarget.ts sshArgs, this host's key), the bytes into `sink`. */
export const sshDeviceReader: DeviceReader = (target, remotePath, maxBytes, sink) => new Promise((done) => {
  let fd: number;
  try { fd = openSync(sink, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600); } catch { done(false); return; }
  let finished = false;
  let n = 0;
  let bad = false;
  const finish = (ok: boolean) => {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    try { closeSync(fd); } catch { /* closed */ }
    done(ok);
  };
  let child: ReturnType<typeof spawn>;
  try {
    child = spawn('ssh', sshArgs(target.machine, remoteReadCommand(remotePath, maxBytes)), { stdio: ['ignore', 'pipe', 'ignore'] });
  } catch { finish(false); return; }
  const timer = setTimeout(() => { bad = true; child.kill('SIGKILL'); }, PULL_TIMEOUT_MS);
  timer.unref?.();
  child.stdout!.on('data', (chunk: Buffer) => {
    if (bad) return;
    n += chunk.length;
    if (n > maxBytes) { bad = true; child.kill('SIGKILL'); return; }
    try { writeSync(fd, chunk); } catch { bad = true; child.kill('SIGKILL'); }
  });
  child.on('error', () => finish(false));
  child.on('close', (code) => finish(!bad && code === 0 && n > 0));
});

/** The registry's view of one seat: on this host, or on a device this host reaches. Read at most every 2 s. */
export function registryLocator(dataDir: string): (agentId: string) => DeviceTarget | null {
  let cache: { at: number; reg: any } | null = null;
  return (agentId) => {
    if (!SAFE_ID.test(agentId)) return null;
    const now = Date.now();
    if (!cache || now - cache.at > 2_000) {
      let reg: any = null;
      try { reg = JSON.parse(readFileSync(join(dataDir, 'registry.json'), 'utf-8')); } catch { reg = null; }
      cache = { at: now, reg };
    }
    const def = cache.reg?.agents && Object.prototype.hasOwnProperty.call(cache.reg.agents, agentId) ? cache.reg.agents[agentId] : null;
    if (!def || isThisHost(def.machine)) return null;
    const machine = machineFrom(cache.reg.machines || {}, String(def.machine), def);
    // A label naming a machine this host cannot reach is treated as here (a mislabelled local seat keeps
    // its images); only a reachable device seat has its paths read over there.
    if (!machine) return null;
    const cwd = typeof def.cwd === 'string' && def.cwd ? def.cwd : machine.home;
    return { machineId: String(def.machine), machine, cwd };
  };
}

export interface DevicePullDeps {
  dataDir: string;
  locate: (agentId: string) => DeviceTarget | null;
  read?: DeviceReader;
  now?: () => number;
  concurrency?: number;
  maxQueue?: number;
}

export class DevicePuller {
  private inflight = new Set<string>();
  private failed = new Map<string, number>();
  private queue: Array<() => Promise<void>> = [];
  private running = 0;
  private waiters: Array<() => void> = [];

  constructor(private readonly deps: DevicePullDeps) {}

  /** Is this seat on another machine (its paths are read there)? */
  isDevice(agentId: string): boolean { return !!this.deps.locate(agentId); }

  /**
   * The file a stamp should look at for the path a seat wrote: the path itself for a seat on this
   * host; for a device seat, the local copy once it is here (else null, and the copy is scheduled).
   * A device path outside the seat's working folder over there is null and never asked for.
   */
  served(agentId: string, path: string): string | null {
    const target = this.deps.locate(agentId);
    if (!target) return path;
    if (!SAFE_ID.test(agentId) || !deviceRemoteOk(path, target.cwd)) return null;
    const local = deviceCopyPath(this.deps.dataDir, agentId, target.machineId, path);
    if (existsSync(local)) return local;
    this.schedule(agentId, target, path, local);
    return null;
  }

  private schedule(agentId: string, target: DeviceTarget, path: string, local: string): void {
    const key = `${agentId}\0${target.machineId}\0${path}`;
    const now = this.deps.now ? this.deps.now() : Date.now();
    const until = this.failed.get(key);
    if (until !== undefined) { if (until > now) return; this.failed.delete(key); }
    if (this.inflight.has(key) || this.queue.length >= (this.deps.maxQueue ?? 64)) return;
    this.inflight.add(key);
    this.queue.push(async () => {
      const ok = await this.pull(target, path, local).catch(() => false);
      this.inflight.delete(key);
      if (!ok) {
        this.failed.set(key, (this.deps.now ? this.deps.now() : Date.now()) + FAIL_TTL_MS);
        if (this.failed.size > 4096) for (const k of this.failed.keys()) { this.failed.delete(k); if (this.failed.size <= 2048) break; }
      }
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

  /** Copy one file: into a fresh temp name in the seat's folder, checked (a regular file, an image or a video by its
   *  bytes, under that kind's cap), then renamed into place. Anything else leaves nothing behind. */
  private async pull(target: DeviceTarget, path: string, local: string): Promise<boolean> {
    const dir = local.slice(0, local.lastIndexOf(sep));
    try { mkdirSync(dir, { recursive: true, mode: 0o700 }); } catch { return false; }
    const tmp = join(dir, `.pull-${randomBytes(8).toString('hex')}.part`);
    const read = this.deps.read ?? sshDeviceReader;
    let ok = false;
    try {
      ok = await read(target, path, MAX_VIDEO_BYTES, tmp);
      if (ok) ok = checkCopy(tmp);
      if (ok) renameSync(tmp, local);
      return ok;
    } catch { return false; } finally {
      if (!ok) { try { unlinkSync(tmp); } catch { /* never created */ } }
    }
  }

  /** Tests: resolves once nothing is queued or running. */
  idle(): Promise<void> {
    if (!this.running && !this.queue.length) return Promise.resolve();
    return new Promise((r) => this.waiters.push(r));
  }
}

function checkCopy(file: string): boolean {
  let fd: number;
  try { fd = openSync(file, constants.O_RDONLY | constants.O_NOFOLLOW); } catch { return false; }
  try {
    const st = fstatSync(fd);
    if (!st.isFile() || st.size === 0) return false;
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
