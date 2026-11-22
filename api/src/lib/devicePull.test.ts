/**
 * Media from a connected device (lib/devicePull.ts): a seat on another machine names a file THERE;
 * it is copied over the one existing cross-machine path (SSH with this host's key, lib/machineTarget.ts)
 * into the seat's own folder under the data dir, then stamped and served by the same allow-list
 * as any local file. Proven with a stand-in `ssh` on PATH that runs the remote command on this box
 * (so the real argv and the real remote command are what run), and with an injected reader.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync, symlinkSync, utimesSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from './testTmp.js';
import { DevicePuller, PROTECT_MS, REFRESH_MS, deviceCopyPath, deviceDir, deviceRemoteOk, registryLocator, remoteReadCommand, remoteStatCommand, setLocalSeatEvidence, sshDeviceReader, sshDeviceStat, type DeviceTarget, type RemoteStat } from './devicePull.js';
import { MediaStore, agentMediaRoots, imageRefsFor, openMedia, _resetMisses } from './mediaStore.js';
import { sshArgs } from './machineTarget.js';

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(200, 9)]);
const MP4 = (() => {
  const h = Buffer.alloc(24); h.writeUInt32BE(24, 0); h.write('ftypisom', 4, 'latin1'); h.writeUInt32BE(0x200, 12); h.write('isomiso2', 16, 'latin1');
  return Buffer.concat([h, Buffer.alloc(500, 3)]);
})();
const MACHINE = { hostname: 'studio', tailscale_ip: '100.64.0.9', ssh_user: 'dev', home: '/home/dev' };
// The install's puller (agentMediaRoots) asks the registered evidence; here nothing runs locally.
setLocalSeatEvidence(() => false);
const NO_LOCAL = { evidence: () => false };

/** A data dir whose registry puts seat `mac-seat` on machine `studio`, its cwd (over there) `remoteCwd`. */
function install(remoteCwd: string) {
  const data = testTmpDir('device-data-');
  mkdirSync(join(data, 'state', 'agents'), { recursive: true });
  writeFileSync(join(data, 'registry.json'), JSON.stringify({
    machines: { studio: MACHINE },
    agents: { 'mac-seat': { machine: 'studio', cwd: remoteCwd }, 'here-seat': { machine: 'local', cwd: '/srv/here' }, 'lost-seat': { machine: 'nowhere' } },
  }));
  writeFileSync(join(data, 'state', 'agents', 'mac-seat.json'), JSON.stringify({ cwd: remoteCwd }));
  return data;
}

test('the registry decides who is a device seat: a reachable machine only; here and an unknown machine are local', () => {
  const data = install('/Users/dev/proj');
  const locate = registryLocator(data, NO_LOCAL);
  assert.deepEqual(locate('mac-seat'), { machineId: 'studio', machine: MACHINE, cwd: '/Users/dev/proj' });
  assert.equal(locate('here-seat'), null);
  assert.equal(locate('lost-seat'), null, 'a label naming no reachable machine keeps the local rules');
  assert.equal(locate('nobody'), null);
  assert.equal(locate('../x'), null);
  assert.equal(locate('constructor'), null);
  // No evidence source at all: nothing proves the seat is elsewhere, so it is here.
  setLocalSeatEvidence(null);
  try { assert.equal(registryLocator(data)('mac-seat'), null); } finally { setLocalSeatEvidence(() => false); }
});

test('a device seat\'s roots are only its own data-dir folders: its cwd names a folder over there, never one here', () => {
  const data = install('/srv/shared');
  assert.deepEqual(agentMediaRoots(data, 'mac-seat'), [join(data, 'state', 'shots', 'mac-seat'), join(data, 'state', 'agents', 'mac-seat')]);
});

test('which device paths may be asked for: under the seat\'s cwd over there, safe characters, no traversal', () => {
  const cwd = '/Users/dev/proj';
  assert.equal(deviceRemoteOk('/Users/dev/proj/out/shot.png', cwd), true);
  for (const bad of ['/Users/dev/other/x.png', '/Users/dev/proj', '/Users/dev/proj/../.ssh/id.png', '/Users/dev/proj/./x.png',
    '/Users/dev/proj//x.png', "/Users/dev/proj/it's.png", '/Users/dev/proj/$(id).png', '/Users/dev/proj/a b.png', '/Users/dev/projx/a.png', 'rel/a.png']) {
    assert.equal(deviceRemoteOk(bad, cwd), false, bad);
  }
  assert.equal(deviceRemoteOk('/etc/x.png', '/'), false, 'a seat whose cwd is / may be asked for nothing');
  assert.throws(() => remoteReadCommand("/a/it's.png", 10));
  const cmd = remoteReadCommand('/Users/dev/proj/out/shot.png', 100);
  assert.match(cmd, /^p='\/Users\/dev\/proj\/out\/shot\.png'; /);
  assert.match(cmd, /head -c 101 "\$p"$/);
});

/** A stand-in `ssh` that checks the options every cross-machine call uses, then runs the remote command here. */
function fakeSsh(log: string) {
  const bin = testTmpDir('device-bin-');
  writeFileSync(join(bin, 'ssh'), `#!/bin/sh
printf '%s\\n' "$@" > '${log}'
for last; do :; done
exec sh -c "$last"
`);
  chmodSync(join(bin, 'ssh'), 0o755);
  return bin;
}

test('end to end over the real reader: the copy lands in the seat\'s own folder, is stamped with the path it wrote, and served', async () => {
  _resetMisses();
  const remote = testTmpDir('device-remote-');   // stands in for the device's disk
  const cwd = join(remote, 'proj');
  mkdirSync(join(cwd, 'out'), { recursive: true });
  writeFileSync(join(cwd, 'out', 'shot.png'), PNG);
  writeFileSync(join(cwd, 'out', 'demo.mp4'), MP4);
  writeFileSync(join(remote, 'secret.png'), PNG);
  symlinkSync(join(remote, 'secret.png'), join(cwd, 'out', 'link.png'));
  writeFileSync(join(cwd, 'out', 'page.png'), '<html>not an image</html>');
  const data = install(cwd);
  const log = join(testTmpDir('device-log-'), 'argv');
  const prevPath = process.env.PATH;
  process.env.PATH = `${fakeSsh(log)}:${prevPath}`;
  try {
    const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data, NO_LOCAL) });
    const store = new MediaStore(join(data, 'state', 'media-ids.json'));
    const deps = { store, roots: (a: string) => agentMediaRoots(data, a), serve: (a: string, p: string) => puller.served(a, p) };
    const named = ['shot.png', 'demo.mp4', 'link.png', 'page.png'].map((n) => join(cwd, 'out', n));
    // First stamp: nothing is here yet, so nothing is stamped; the copies are scheduled.
    assert.deepEqual(imageRefsFor('mac-seat', named, deps), []);
    await puller.idle();
    // The argv is the cross-machine one: this host's key, IdentitiesOnly, the registry's user@ip.
    const argv = readFileSync(log, 'utf-8').trim().split('\n');
    assert.deepEqual(argv.slice(0, -1), sshArgs(MACHINE, 'x').slice(0, -1));
    const refs = imageRefsFor('mac-seat', named, deps);
    assert.deepEqual(refs.map((r) => [r.path, r.kind]), [[named[0], undefined], [named[1], 'video']], 'the link and the non-image never arrive');
    // The ids serve the COPIES, under the seat's own folder; the ref keeps the path the seat wrote.
    const row = store.lookup(refs[0].id)!;
    const v = (f: string): RemoteStat => { const st = statSync(f); return { size: st.size, mtime: Math.floor(st.mtimeMs / 1000) }; };
    assert.equal(row.path, deviceCopyPath(data, 'mac-seat', 'studio', named[0], v(named[0])));
    assert.ok(row.path.startsWith(join(data, 'state', 'agents', 'mac-seat', 'device') + '/'));
    assert.equal(row.ref, named[0]);
    const opened = openMedia(row.path, agentMediaRoots(data, 'mac-seat'));
    assert.ok(opened.ok);
    assert.deepEqual(readFileSync(row.path), PNG);
    // Nothing else was left behind: two copies, no temp files.
    assert.equal(readdirSync(join(data, 'state', 'agents', 'mac-seat', 'device')).length, 2);
    // A second stamp answers from the table and the copies: no new pull.
    writeFileSync(log, '');
    assert.deepEqual(imageRefsFor('mac-seat', named.slice(0, 2), deps), refs);
    await puller.idle();
    assert.equal(readFileSync(log, 'utf-8'), '', 'no ssh for a path already copied');
  } finally { process.env.PATH = prevPath; }
});

test('SABOTAGE containment: a device path outside the seat\'s cwd, or with traversal, is never asked for; a failed pull is not retried at once', async () => {
  const data = install('/Users/dev/proj');
  const asked: string[] = [];
  let t = 1_000;
  const reader = async (_t: DeviceTarget, p: string, _max: number, sink: string) => { asked.push(p); writeFileSync(sink, 'not media'); return true; };
  const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data, NO_LOCAL), read: reader, stat: async () => ({ size: 9, mtime: 1 }), now: () => t });
  for (const p of ['/Users/dev/.ssh/id_ed25519.png', '/Users/dev/proj/../secret.png', '/etc/passwd.png', "/Users/dev/proj/x'.png"]) {
    assert.equal(puller.served('mac-seat', p), null, p);
  }
  await puller.idle();
  assert.deepEqual(asked, [], 'none of them reached the device');
  // A path that is allowed but whose bytes are not media: asked once, nothing kept, not asked again for a minute.
  assert.equal(puller.served('mac-seat', '/Users/dev/proj/fake.png'), null);
  await puller.idle();
  assert.equal(puller.served('mac-seat', '/Users/dev/proj/fake.png'), null);
  await puller.idle();
  assert.deepEqual(asked, ['/Users/dev/proj/fake.png']);
  assert.equal(existsSync(deviceCopyPath(data, 'mac-seat', 'studio', '/Users/dev/proj/fake.png', { size: 9, mtime: 1 })), false);
  assert.deepEqual(readdirSync(join(data, 'state', 'agents', 'mac-seat', 'device')), [], 'no temp file left');
  t += 61_000;
  assert.equal(puller.served('mac-seat', '/Users/dev/proj/fake.png'), null);
  await puller.idle();
  assert.equal(asked.length, 2, 're-asked after the back-off');
  // A seat on this host is untouched: its path is its path, nothing is pulled.
  assert.equal(puller.served('here-seat', '/srv/here/a.png'), '/srv/here/a.png');
});

test('a pull over the image cap is refused for an image (the video cap is the reader\'s bound)', async () => {
  const data = install('/Users/dev/proj');
  const big = Buffer.concat([PNG.subarray(0, 64), Buffer.alloc(26 * 1024 * 1024)]);
  const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data, NO_LOCAL), stat: async () => ({ size: big.length, mtime: 1 }),
    read: async (_t, _p, _m, sink) => { writeFileSync(sink, big); return true; } });
  assert.equal(puller.served('mac-seat', '/Users/dev/proj/huge.png'), null);
  await puller.idle();
  assert.equal(existsSync(deviceCopyPath(data, 'mac-seat', 'studio', '/Users/dev/proj/huge.png', { size: big.length, mtime: 1 })), false);
});

test('the real reader stops at its byte bound and says so', async () => {
  const remote = testTmpDir('device-remote-big-');
  writeFileSync(join(remote, 'a.png'), Buffer.concat([PNG, Buffer.alloc(5000)]));
  const log = join(testTmpDir('device-log-'), 'argv');
  const prevPath = process.env.PATH;
  process.env.PATH = `${fakeSsh(log)}:${prevPath}`;
  try {
    const sink = join(testTmpDir('device-sink-'), 'x.part');
    const ok = await sshDeviceReader({ machineId: 'studio', machine: MACHINE, cwd: remote }, join(remote, 'a.png'), 1000, sink);
    assert.equal(ok, false, 'more than the bound arrived');
    const sink2 = join(testTmpDir('device-sink-'), 'y.part');
    assert.equal(await sshDeviceReader({ machineId: 'studio', machine: MACHINE, cwd: remote }, join(remote, 'a.png'), 10_000, sink2), true);
    assert.equal(readFileSync(sink2).length, PNG.length + 5000);
  } finally { process.env.PATH = prevPath; }
});

// ---- #601 review finding 1: the label is a label, not evidence -------------------------------------------------

test('SABOTAGE finding 1: a seat labelled with a reachable machine whose session or transcript is HERE keeps its local images and is never pulled', async () => {
  _resetMisses();
  const here = testTmpDir('device-here-');          // the seat's cwd, on THIS host
  mkdirSync(join(here, 'out'), { recursive: true });
  writeFileSync(join(here, 'out', 'shot.png'), PNG);
  const data = install(here);
  const asked: string[] = [];
  const reader = async (_t: DeviceTarget, p: string) => { asked.push(`read ${p}`); return false; };
  const stat = async (_t: DeviceTarget, p: string) => { asked.push(`stat ${p}`); return null; };
  const seen: string[] = [];
  // Evidence that it runs here: what routes/chat-transcript.ts seatRunsHere answers (its own tmux session or transcript).
  const evidence = (id: string, def: Record<string, unknown>) => { seen.push(`${id}:${def.machine}`); return id === 'mac-seat'; };
  const locate = registryLocator(data, { evidence });
  assert.equal(locate('mac-seat'), null, 'local evidence outranks the label');
  assert.deepEqual(seen, ['mac-seat:studio'], 'the evidence is asked for the labelled seat');
  assert.equal(locate('here-seat'), null);
  assert.deepEqual(seen, ['mac-seat:studio'], 'a seat labelled for this host is not even asked about');
  const puller = new DevicePuller({ dataDir: data, locate, read: reader, stat });
  const shot = join(here, 'out', 'shot.png');
  assert.equal(puller.served('mac-seat', shot), shot, 'its path is its path');
  assert.equal(puller.isDevice('mac-seat'), false);
  const store = new MediaStore(join(data, 'state', 'media-ids.json'));
  const roots = [here, join(data, 'state', 'shots', 'mac-seat'), join(data, 'state', 'agents', 'mac-seat')];
  const refs = imageRefsFor('mac-seat', [shot], { store, roots: () => roots, serve: (a, p) => puller.served(a, p) });
  assert.deepEqual(refs.map((r) => r.name), ['shot.png'], 'the image it showed before is still shown');
  await puller.idle();
  assert.deepEqual(asked, [], 'no copy was scheduled');
  assert.equal(existsSync(deviceDir(data, 'mac-seat')), false);
  // The install's roots: its cwd is a root again when the registered evidence says it runs here.
  setLocalSeatEvidence((id) => id === 'mac-seat');
  try {
    const d2 = install(here);
    assert.deepEqual(agentMediaRoots(d2, 'mac-seat'), [here, join(d2, 'state', 'shots', 'mac-seat'), join(d2, 'state', 'agents', 'mac-seat')]);
  } finally { setLocalSeatEvidence(() => false); }
  // A throwing evidence source fails toward "here" too (no SSH on a guess).
  assert.equal(registryLocator(data, { evidence: () => { throw new Error('x'); } })('mac-seat'), null);
});

// ---- #601 review finding 2: device copies are bounded and refreshed -------------------------------------------

/** An in-memory device: files by path, each with its bytes and mtime; counts what was asked. */
function device() {
  const files = new Map<string, { bytes: Buffer; mtime: number }>();
  const asked = { stat: [] as string[], read: [] as string[] };
  const stat = async (_t: DeviceTarget, p: string): Promise<RemoteStat | null> => {
    asked.stat.push(p);
    const f = files.get(p);
    return f ? { size: f.bytes.length, mtime: f.mtime } : null;
  };
  const read = async (_t: DeviceTarget, p: string, max: number, sink: string) => {
    asked.read.push(p);
    const f = files.get(p);
    if (!f || f.bytes.length > max) return false;
    writeFileSync(sink, f.bytes, { flag: 'wx' });
    return true;
  };
  return { files, asked, stat, read };
}
const png = (n: number, fill = 1) => Buffer.concat([PNG.subarray(0, 8), Buffer.alloc(n, fill)]);

test('SABOTAGE finding 2 (refresh): a device file that changed is copied again under its new version; one that did not is only re-checked', async () => {
  _resetMisses();
  const data = install('/Users/dev/proj');
  const dev = device();
  const p = '/Users/dev/proj/out/shot.png';
  dev.files.set(p, { bytes: png(100, 1), mtime: 1000 });
  let t = 5_000_000;
  const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data, { ...NO_LOCAL, now: () => t }), read: dev.read, stat: dev.stat, now: () => t });
  const store = new MediaStore(join(data, 'state', 'media-ids.json'));
  const deps = { store, roots: (a: string) => agentMediaRoots(data, a), serve: (a: string, q: string) => puller.served(a, q), now: () => t };
  assert.deepEqual(imageRefsFor('mac-seat', [p], deps), []);
  await puller.idle();
  const first = imageRefsFor('mac-seat', [p], deps);
  assert.equal(first.length, 1);
  const v1 = deviceCopyPath(data, 'mac-seat', 'studio', p, { size: 108, mtime: 1000 });
  assert.equal(store.lookup(first[0].id)?.path, v1);
  assert.deepEqual(dev.asked, { stat: [p], read: [p] });
  // Within REFRESH_MS: the copy is served, the device is not asked.
  t += REFRESH_MS - 1;
  assert.deepEqual(imageRefsFor('mac-seat', [p], deps), first);
  await puller.idle();
  assert.equal(dev.asked.stat.length, 1);
  // Past it, unchanged on the device: one check, no bytes, the same id.
  t += 2;
  assert.deepEqual(imageRefsFor('mac-seat', [p], deps), first);
  await puller.idle();
  assert.deepEqual(dev.asked, { stat: [p, p], read: [p] });
  assert.deepEqual(imageRefsFor('mac-seat', [p], deps), first);
  // The seat overwrote it (a screenshot loop): the next check copies the new version, the old copy goes.
  dev.files.set(p, { bytes: png(150, 2), mtime: 1060 });
  t += REFRESH_MS;
  assert.deepEqual(imageRefsFor('mac-seat', [p], deps), first, 'the old copy serves until the new one is here');
  await puller.idle();
  assert.deepEqual(dev.asked.read, [p, p]);
  const v2 = deviceCopyPath(data, 'mac-seat', 'studio', p, { size: 158, mtime: 1060 });
  assert.equal(existsSync(v2), true);
  assert.equal(existsSync(v1), false, 'the previous version is removed');
  assert.deepEqual(readFileSync(v2), png(150, 2));
  const second = imageRefsFor('mac-seat', [p], deps);
  assert.equal(second.length, 1);
  assert.notEqual(second[0].id, first[0].id, 'a new version, a new URL (no cached old frame)');
  assert.equal(store.lookup(first[0].id), null, 'one row per path the seat wrote: the old version\'s row is gone');
  assert.equal(store.countFor('mac-seat'), 1);
  assert.deepEqual(readdirSync(deviceDir(data, 'mac-seat')), [v2.slice(v2.lastIndexOf('/') + 1)]);
  // A change seen in the middle of a copy (the bytes are not the size the check saw) keeps nothing.
  dev.files.set(p, { bytes: png(150, 3), mtime: 1120 });
  const realRead = dev.read;
  const growing = async (tg: DeviceTarget, q: string, max: number, sink: string) => {
    const ok = await realRead(tg, q, max, sink);
    writeFileSync(sink, png(149, 3));
    return ok;
  };
  const p2 = new DevicePuller({ dataDir: data, locate: registryLocator(data, NO_LOCAL), read: growing, stat: dev.stat });
  const other = '/Users/dev/proj/out/other.png';
  dev.files.set(other, { bytes: png(150, 3), mtime: 1 });
  assert.equal(p2.served('mac-seat', other), null);
  await p2.idle();
  assert.equal(existsSync(deviceCopyPath(data, 'mac-seat', 'studio', other, { size: 158, mtime: 1 })), false);
});

test('SABOTAGE finding 2 (bound): the device folder keeps at most maxCopies / maxBytes; the least recently stamped go, never one on screen', async () => {
  const data = install('/Users/dev/proj');
  const dev = device();
  const path = (i: number) => `/Users/dev/proj/out/s${i}.png`;
  for (let i = 0; i < 8; i++) dev.files.set(path(i), { bytes: png(100, i + 1), mtime: 10 + i });
  let t = 1_000_000_000;
  const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data, { ...NO_LOCAL, now: () => t }), read: dev.read, stat: dev.stat, now: () => t, maxCopies: 3 });
  const dir = deviceDir(data, 'mac-seat');
  const files = () => readdirSync(dir).filter((f) => !f.startsWith('.')).length;
  const copy = (i: number) => deviceCopyPath(data, 'mac-seat', 'studio', path(i), { size: 108, mtime: 10 + i });
  for (const i of [0, 1, 2]) { puller.served('mac-seat', path(i)); await puller.idle(); t += 1000; }
  assert.equal(files(), 3);
  // All three were stamped just now: a fourth does not push one of them out; only its size was asked.
  const reads = dev.asked.read.length;
  assert.equal(puller.served('mac-seat', path(3)), null);
  await puller.idle();
  assert.equal(files(), 3);
  assert.equal(existsSync(copy(3)), false);
  assert.equal(dev.asked.read.length, reads, 'no bytes crossed for a copy that has no room');
  // Later, s1 and s2 are still on screen (stamped again), s0 is not: s0 goes to make room for s3.
  t += PROTECT_MS + 61_000;
  puller.served('mac-seat', path(1));
  puller.served('mac-seat', path(2));
  await puller.idle();
  puller.served('mac-seat', path(3));
  await puller.idle();
  assert.equal(files(), 3, 'still at most 3');
  assert.equal(existsSync(copy(0)), false, 'the least recently stamped went');
  for (const i of [1, 2, 3]) assert.equal(existsSync(copy(i)), true, `s${i} kept`);
  assert.equal(puller.served('mac-seat', path(0)), null, 'a pruned copy is pulled again when it is shown again');
  // A leftover temp file of an interrupted copy goes too.
  const stale = join(dir, '.pull-0000000000000000.part');
  writeFileSync(stale, 'x');
  utimesSync(stale, new Date(0), new Date(0));
  t += PROTECT_MS + 61_000;
  puller.served('mac-seat', path(4));
  await puller.idle();
  assert.equal(existsSync(stale), false);
  assert.ok(files() <= 3);
});

test('finding 2 (bytes): a copy bigger than the folder\'s byte bound is never read; others make room by size', async () => {
  const data = install('/Users/dev/proj');
  const dev = device();
  dev.files.set('/Users/dev/proj/a.png', { bytes: png(600), mtime: 1 });
  dev.files.set('/Users/dev/proj/b.png', { bytes: png(600), mtime: 1 });
  dev.files.set('/Users/dev/proj/huge.png', { bytes: png(2000), mtime: 1 });
  let t = 1_000_000_000;
  const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data, { ...NO_LOCAL, now: () => t }), read: dev.read, stat: dev.stat, now: () => t, maxBytes: 1000 });
  puller.served('mac-seat', '/Users/dev/proj/huge.png');
  await puller.idle();
  assert.deepEqual(dev.asked.read, [], 'over the bound: only its size was asked');
  puller.served('mac-seat', '/Users/dev/proj/a.png'); await puller.idle();
  t += PROTECT_MS + 1;
  puller.served('mac-seat', '/Users/dev/proj/b.png'); await puller.idle();
  const left = readdirSync(deviceDir(data, 'mac-seat'));
  assert.equal(left.length, 1, 'two of 608 bytes do not fit in 1000: a went');
  assert.match(left[0], /-b\.png$/);
});

test('the remote check command: the same refusals as the copy, then size and mtime; the real statter over the stand-in ssh', async () => {
  const cmd = remoteStatCommand('/Users/dev/proj/out/shot.png');
  assert.match(cmd, /^p='\/Users\/dev\/proj\/out\/shot\.png'; r=\$\(realpath "\$p" 2>\/dev\/null\) \|\| exit 3; \[ "\$r" = "\$p" \] \|\| exit 4; \[ -f "\$p" \] \|\| exit 5; /);
  assert.throws(() => remoteStatCommand("/a/it's.png"));
  const remote = testTmpDir('device-remote-stat-');
  writeFileSync(join(remote, 'a.png'), png(50));
  utimesSync(join(remote, 'a.png'), new Date(1_700_000_000_000), new Date(1_700_000_000_000));
  symlinkSync(join(remote, 'a.png'), join(remote, 'l.png'));
  const log = join(testTmpDir('device-log-'), 'argv');
  const prevPath = process.env.PATH;
  process.env.PATH = `${fakeSsh(log)}:${prevPath}`;
  try {
    const target = { machineId: 'studio', machine: MACHINE, cwd: remote };
    assert.deepEqual(await sshDeviceStat(target, join(remote, 'a.png')), { size: 58, mtime: 1_700_000_000 });
    assert.deepEqual(readFileSync(log, 'utf-8').trim().split('\n').slice(0, -1), sshArgs(MACHINE, 'x').slice(0, -1), 'the cross-machine argv');
    assert.equal(await sshDeviceStat(target, join(remote, 'l.png')), null, 'a link is refused');
    assert.equal(await sshDeviceStat(target, join(remote, 'none.png')), null);
  } finally { process.env.PATH = prevPath; }
});
