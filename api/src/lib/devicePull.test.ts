/**
 * Media from a connected device (lib/devicePull.ts): a seat on another machine names a file THERE;
 * it is copied over the one existing cross-machine path (SSH with this host's key, lib/machineTarget.ts)
 * into the seat's own folder under the data dir, then stamped and served by the same allow-list
 * as any local file. Proven with a stand-in `ssh` on PATH that runs the remote command on this box
 * (so the real argv and the real remote command are what run), and with an injected reader.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdirSync, readFileSync, readdirSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from './testTmp.js';
import { DevicePuller, deviceCopyPath, deviceRemoteOk, registryLocator, remoteReadCommand, sshDeviceReader, type DeviceTarget } from './devicePull.js';
import { MediaStore, agentMediaRoots, imageRefsFor, openMedia, _resetMisses } from './mediaStore.js';
import { sshArgs } from './machineTarget.js';

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(200, 9)]);
const MP4 = (() => {
  const h = Buffer.alloc(24); h.writeUInt32BE(24, 0); h.write('ftypisom', 4, 'latin1'); h.writeUInt32BE(0x200, 12); h.write('isomiso2', 16, 'latin1');
  return Buffer.concat([h, Buffer.alloc(500, 3)]);
})();
const MACHINE = { hostname: 'studio', tailscale_ip: '100.64.0.9', ssh_user: 'dev', home: '/home/dev' };

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
  const locate = registryLocator(data);
  assert.deepEqual(locate('mac-seat'), { machineId: 'studio', machine: MACHINE, cwd: '/Users/dev/proj' });
  assert.equal(locate('here-seat'), null);
  assert.equal(locate('lost-seat'), null, 'a label naming no reachable machine keeps the local rules');
  assert.equal(locate('nobody'), null);
  assert.equal(locate('../x'), null);
  assert.equal(locate('constructor'), null);
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
    const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data) });
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
    assert.equal(row.path, deviceCopyPath(data, 'mac-seat', 'studio', named[0]));
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
  const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data), read: reader, now: () => t });
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
  assert.equal(existsSync(deviceCopyPath(data, 'mac-seat', 'studio', '/Users/dev/proj/fake.png')), false);
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
  const puller = new DevicePuller({ dataDir: data, locate: registryLocator(data), read: async (_t, _p, _m, sink) => { writeFileSync(sink, big); return true; } });
  assert.equal(puller.served('mac-seat', '/Users/dev/proj/huge.png'), null);
  await puller.idle();
  assert.equal(existsSync(deviceCopyPath(data, 'mac-seat', 'studio', '/Users/dev/proj/huge.png')), false);
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
