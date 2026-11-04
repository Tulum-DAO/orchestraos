/**
 * Settings › Agents "Background processes" (lib/backgroundProcesses.ts) on a fake /proc: which listening programs are
 * candidates (ownership, the denylist, the parent chain), who started them, and Stop's re-check.
 * Run: npx tsx --test src/lib/backgroundProcesses.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  listCandidates, stopCandidate, parseNetTcp, parseStat, isRuntime, isInstallService, supervisorSeat, maskArgv, programName, programDetail,
  commandHash, restartedOn, type ProcInfo, type ProcSource,
} from './backgroundProcesses.js';

const ROOT = '/home/orchestra/orchestraos';
const UID = 1000;
const opts = { codeRoot: ROOT, installPorts: [8888, 8891, 8890, 5071] };
const seats = [{ id: 'dev-a', tmuxSession: 'dev-a' }, { id: 'qa-b', tmuxSession: 'qa-b-gen2' }];

type P = Partial<ProcInfo> & { pid: number; ppid: number; argv: string[] };
function fake(procs: P[], listen: Record<number, number[]>, panes: Record<number, string> = {}):
    ProcSource & { table: Map<number, ProcInfo>; listening: Map<number, number[]> } {
  const listening = new Map(Object.entries(listen).map(([k, v]) => [Number(k), v] as [number, number[]]));
  const table = new Map<number, ProcInfo>(procs.map((p) => [p.pid, {
    uid: UID, startTicks: 1000 + p.pid, state: 'S', cwd: '/tmp', cgroup: '0::/user.slice/user-1000.slice/session-5.scope', agentEnv: null, ...p,
  }]));
  return {
    table, listening, uid: UID, selfPid: 500, bootTimeSec: 1_800_000_000, clkTck: 100, nowTicks: () => 4000,
    listeners: () => new Map([...listening].filter(([pid]) => table.has(pid))),
    proc: (pid) => table.get(pid) ?? null,
    tmuxPanes: async () => new Map(Object.entries(panes).map(([k, v]) => [Number(k), v])),
  };
}

// A machine: init(1), systemd --user(9), the install (orchestra_cli up 400 -> API 500, gateway 501, proxy 502, arturo 503),
// tmux server 10 with seat panes (dev-a's bash 20, qa-b-gen2's bash 30) and a login pane (40), an ssh session (60).
const base: P[] = [
  { pid: 9, ppid: 1, argv: ['/usr/lib/systemd/systemd', '--user'] },
  { pid: 400, ppid: 1, argv: [`${ROOT}/.venv/bin/python`, '-m', 'orchestra_cli', 'up'], cwd: ROOT },
  { pid: 500, ppid: 400, argv: ['node', `${ROOT}/api/dist/server.js`], cwd: ROOT },
  { pid: 501, ppid: 400, argv: [`${ROOT}/.venv/bin/python`, `${ROOT}/scripts/watch_gateway.py`], cwd: ROOT },
  { pid: 502, ppid: 400, argv: ['node', `${ROOT}/dashboard-proxy.js`], cwd: ROOT },
  { pid: 503, ppid: 400, argv: ['python3', 'services/arturo/arturo-proxy.py'], cwd: ROOT },
  { pid: 10, ppid: 1, argv: ['tmux', 'new-session', '-d', '-s', 'gm'] },
  { pid: 20, ppid: 10, argv: ['-bash'] },
  { pid: 21, ppid: 20, argv: ['claude', '--dangerously-skip-permissions'], agentEnv: 'dev-a' },
  { pid: 22, ppid: 21, argv: ['/bin/bash', '-c', 'npm run dev'], agentEnv: 'dev-a' },
  { pid: 30, ppid: 10, argv: ['-bash'] },
  { pid: 31, ppid: 30, argv: ['python3', `${ROOT}/scripts/seat_supervisor.py`, 'run', 'qa-b'] },
  { pid: 32, ppid: 31, argv: ['node', '/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js', '-p'] },   // env not readable: the supervisor names it
  { pid: 40, ppid: 10, argv: ['bash', '-i'] },
  { pid: 60, ppid: 1, argv: ['sshd: sam@pts/1'] },
  { pid: 61, ppid: 60, argv: ['-bash'] },
];

test('lists only agents\' leftovers: the install, runtimes, MCP, plumbing, other users and person terminals are not', async () => {
  const src = fake([
    ...base,
    { pid: 100, ppid: 22, argv: ['node', '/tmp/app/node_modules/.bin/vite', '--port', '5173'] },              // dev-a's dev server
    { pid: 101, ppid: 32, argv: ['python3', '-m', 'http.server', '9000'] },                                 // qa-b (headless) via supervisor
    { pid: 102, ppid: 1, argv: ['node', 'server.js'], agentEnv: 'dev-a', cgroup: '0::/user.slice/user-1000.slice/user@1000.service/tmux-spawn-x.scope' }, // orphaned, env says dev-a
    { pid: 103, ppid: 9, argv: ['node', 'leftover.js'] },                                                  // orphan to systemd --user, unknown
    { pid: 110, ppid: 500, argv: ['node', 'worker.js'] },                                                  // child of the API
    { pid: 111, ppid: 501, argv: ['python3', 'helper.py'] },                                               // child of the gateway
    { pid: 112, ppid: 21, argv: ['npx', '@playwright/mcp', '--port', '8931'] },                             // an MCP server
    { pid: 113, ppid: 112, argv: ['/opt/chrome/chrome', '--remote-debugging-port=9222'] },                   // the MCP's browser
    { pid: 114, ppid: 61, argv: ['python3', '-m', 'http.server', '8000'] },                                // the operator's own ssh shell
    { pid: 115, ppid: 40, argv: ['node', 'x.js'] },                                                        // a login pane, not a seat
    { pid: 116, ppid: 1, argv: ['/usr/sbin/sshd', '-D'] },
    { pid: 117, ppid: 1, argv: ['/usr/sbin/tailscaled'], uid: 0 },
    { pid: 118, ppid: 1, argv: ['node', 'app.js'], uid: 1001 },                                            // another user
    { pid: 119, ppid: 9, argv: ['node', 'svc.js'], cgroup: '0::/user.slice/user-1000.slice/user@1000.service/app.slice/my.service' },
    { pid: 120, ppid: 1, argv: ['python3', `${ROOT}/scripts/some_daemon.py`], cwd: ROOT },                  // an install script
    { pid: 121, ppid: 22, argv: ['node', 'server.js'] },                                                    // listens on the API's port
    { pid: 122, ppid: 20, argv: ['claude'] },                                                              // the runtime itself
    { pid: 123, ppid: 1, argv: [`${ROOT}/.venv/bin/python`, '-m', 'scripts.identity_store.regenerator', 'run'] },
  ], {
    500: [8888], 501: [8890], 502: [8891], 503: [5071],
    100: [5173, 5173], 101: [9000], 102: [3000], 103: [4000], 110: [7000], 111: [7001], 112: [8931], 113: [9222], 114: [8000],
    115: [8001], 116: [22], 117: [443], 118: [3001], 119: [3002], 120: [3003], 121: [8888], 122: [3004], 123: [3005],
  }, { 20: 'dev-a', 30: 'qa-b-gen2', 40: 'login-install' });
  const got = await listCandidates(src, seats, opts);
  assert.deepEqual(got.map((c) => [c.pid, c.agent, c.via, c.ports]), [
    [100, 'dev-a', 'runtime', [5173]],
    [101, 'qa-b', 'supervisor', [9000]],
    [102, 'dev-a', 'env', [3000]],
  ]);   // not 103: an orphan with no agent id
  assert.equal(got[0].command, 'node /tmp/app/node_modules/.bin/vite --port 5173');
  assert.equal(got[0].program, 'node');
  assert.equal(got[0].detail, 'node vite');
  assert.equal(got[0].commandHash, commandHash(['node', '/tmp/app/node_modules/.bin/vite', '--port', '5173']));
  assert.equal(got[0].startedAt, new Date((1_800_000_000 + 1100 / 100) * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z'));
});

test('attribution: a seat pane with no runtime counts; an orphan whose env id is not a registered seat is not listed', async () => {
  const src = fake([...base,
    { pid: 200, ppid: 30, argv: ['node', 'srv.js'] },                                // in qa-b's pane (session qa-b-gen2)
    { pid: 201, ppid: 1, argv: ['node', 'srv.js'], agentEnv: 'someone-else' },       // orphan, unknown id
    { pid: 202, ppid: 61, argv: ['claude'] },                                        // a person's own claude over ssh…
    { pid: 203, ppid: 202, argv: ['node', 'dev.js'] },                               // …and what it started: listed, no agent
  ], { 200: [5000], 201: [5001], 203: [5002] }, { 30: 'qa-b-gen2' });
  assert.deepEqual((await listCandidates(src, seats, opts)).map((c) => [c.pid, c.agent, c.via]),
    [[200, 'qa-b', 'pane'], [203, null, null]]);
});

test('orphans: an ssh -fN -L tunnel (or autossh) is never listed; a nohup server only with a registered agent id', async () => {
  const orphan = (pid: number, argv: string[], agentEnv: string | null = null): P => ({ pid, ppid: 1, argv, agentEnv });
  const src = fake([...base,
    orphan(300, ['ssh', '-fN', '-L', '5432:db.internal:5432', 'sam@bastion']),
    orphan(301, ['ssh', '-fN', '-L', '5433:db:5432', 'bastion'], 'dev-a'),            // even with an agent id
    orphan(302, ['/usr/lib/autossh/autossh', '-M', '0', '-N', '-L', '6000:x:6000', 'h'], 'dev-a'),
    orphan(303, ['python3', '-m', 'http.server', '8000']),                              // nohup, no agent id
    orphan(304, ['python3', '-m', 'http.server', '8001'], 'dev-a'),                     // nohup, a registered agent
    orphan(305, ['python3', '-m', 'http.server', '8002'], 'not-a-seat'),
  ], { 300: [5432], 301: [5433], 302: [6000], 303: [8000], 304: [8001], 305: [8002] });
  assert.deepEqual((await listCandidates(src, seats, opts)).map((c) => [c.pid, c.agent, c.via, c.program]),
    [[304, 'dev-a', 'env', 'python3']]);
});

test('secrets in command lines are masked; members get the program name only', () => {
  const m = (argv: string[]) => maskArgv(argv).join(' ');
  assert.equal(m(['node', 'srv.js', '--token=abc123', '--key=k', '--password=hunter2', '--api-key', 'sk-live-1']),
    'node srv.js --token=*** --key=*** --password=*** --api-key ***');
  assert.equal(m(['mysql', '-u', 'root', '-p', 'hunter2']), 'mysql -u *** -p ***');
  assert.equal(m(['mysql', '-phunter2']), 'mysql -p***');
  assert.equal(m(['psql', 'postgres://sam:s3cret@db:5432/app']), 'psql postgres://***@db:5432/app');  // pragma: allowlist secret
  assert.equal(m(['srv', 'redis://:pw@cache:6379']), 'srv redis://***@cache:6379');
  assert.equal(m(['srv', 'https://user:pa@ss@host/x']), 'srv https://***@host/x');  // pragma: allowlist secret
  assert.equal(m(['npm', 'https://registry.npmjs.org/@types/node']), 'npm https://registry.npmjs.org/@types/node');   // an @ in the path is not a credential
  // code given to an interpreter, in the admin's command too
  assert.equal(m(['node', '--eval=start({key:"x"})']), 'node --eval=***');
  assert.equal(m(['node', '--print=1']), 'node --print=***');
  assert.equal(m(['perl', '-pe', 's/a/b/', 'f.txt']), 'perl -pe *** f.txt');
  assert.equal(m(['perl', '-E', 'say 1']), 'perl -E ***');
  assert.equal(m(['python3', '-c', 'TOKEN="x"']), 'python3 -c ***');
  assert.equal(m(['perl', '-eprint("hunter2")']), 'perl -e***');
  assert.equal(m(['srv', '--db=mongodb://u:p@h/db']), 'srv --db=mongodb://***@h/db');
  // query parameters that name a secret
  assert.equal(m(['curl', 'https://h/api?x=1&access_token=abc&apiKey=k2&sig=zz&page=2']), 'curl https://h/api?x=1&access_token=***&apiKey=***&sig=***&page=2');
  assert.equal(m(['srv', 'http://h/?auth=a;password=b']), 'srv http://h/?auth=***;password=***');
  // Bearer / Basic, alone or inside a header
  assert.equal(m(['curl', '-H', 'Authorization: Bearer abc.def', 'https://h']), 'curl -H Authorization: *** https://h');
  assert.equal(m(['curl', '-H', 'X-Auth: Basic dXNlcjpwYXNz']), 'curl -H X-Auth: Basic ***');
  assert.equal(m(['tool', 'Bearer', 'abc']), 'tool Bearer ***');
  // -u / --user and what follows
  assert.equal(m(['curl', '-u', 'sam:hunter2', 'https://h']), 'curl -u *** https://h');
  assert.equal(m(['curl', '--user', 'sam:hunter2']), 'curl --user ***');
  assert.equal(m(['curl', '--user=sam:hunter2']), 'curl --user=***');
  assert.equal(m(['python3', '-u', 'server.py']), 'python3 -u server.py');   // python's -u is "unbuffered", not a user
  assert.equal(m(['env', 'API_KEY=sk-123', 'PORT=3000', 'node', 'x.js']), 'env API_KEY=*** PORT=*** node x.js');
  assert.equal(m(['srv', 'deadbeefdeadbeefdeadbeefdeadbeef', '--sig=QWxhZGRpbjpvcGVuIHNlc2FtZTEyMzQ1Njc4OTA=']), 'srv *** --sig=***');  // pragma: allowlist secret
  assert.equal(m(['node', '/tmp/app/node_modules/.bin/vite', '--port', '5173', '--host', '0.0.0.0']),
    'node /tmp/app/node_modules/.bin/vite --port 5173 --host 0.0.0.0');
  // a member sees the program's name only: never a script, a flag or inline code
  for (const argv of [['perl', '-E', 'say "hunter2"'], ['perl', '-Bc', 'x'], ['perl', '-pe', 's/a/b/'], ['python3', '-c', 'TOKEN="sk-live-abc"'],
    ['/usr/bin/node', '-e', '{key:"hunter2"}'], ['/usr/local/bin/redis-server', '--requirepass', 'x'], ['python3', '/srv/app/main.py']]) {
    assert.equal(programName(argv), argv[0].split('/').pop());
  }
  // the admin's guess: a script only when it looks like one; a code flag shows the flag only
  const g = programDetail;
  assert.equal(g(['/usr/bin/python3', '-u', '/srv/app/main.py', '--token=x']), 'python3 main.py');
  assert.equal(g(['node', '--inspect', 'server.js']), 'node server.js');
  assert.equal(g(['/usr/local/bin/redis-server', '--requirepass', 'x']), 'redis-server');
  assert.equal(g(['java', '-jar', '/opt/a/app.jar']), 'java app.jar');
  assert.equal(g(['java', '-cp', '/opt/lib/*', 'com.x.Main']), 'java');                 // classpath skipped; a class name is not a script
  assert.equal(g(['python3', '-c', 'import os; TOKEN="sk-live-abc"; serve()']), 'python3 -c');
  assert.equal(g(['node', '-e', 'require("http").createServer().listen({key:"hunter2"})']), 'node -e');
  assert.equal(g(['node', '--eval=start({key:"hunter2"})']), 'node --eval');
  assert.equal(g(['node', '-p', 'x']), 'node -p');
  assert.equal(g(['perl', '-E', 'say 1']), 'perl -E');
  assert.equal(g(['perl', '-Bc', 'x']), 'perl -Bc');
  assert.equal(g(['perl', '-pe', 's/a/b/']), 'perl -pe');
  assert.equal(g(['perl', '-eprint("hunter2")']), 'perl -e');
  assert.equal(g(['ruby', 'TOKEN=abc;x']), 'ruby');                                       // not script-like: nothing guessed
  assert.equal(g(['python3', 'server']), 'python3');                                     // no / and no known extension
  assert.equal(g(['node', '-r', 'dotenv/config', 'server.js']), 'node server.js');
  assert.equal(g(['node', '--import', 'tsx', '--env-file', '.env.local', 'src/main.ts']), 'node main.ts');
  assert.equal(g(['python3', '-X', 'importtime', '-W', 'ignore', 'app.py']), 'python3 app.py');
  assert.equal(g(['python3', '-m', 'http.server', '8000']), 'python3 -m http.server');
  assert.ok(g(['node', `/x/${'a'.repeat(100)}.js`]).length <= 60);
  // the hash is of the full argv, so it tells two commands apart without carrying either
  assert.notEqual(commandHash(['a', '--token=1']), commandHash(['a', '--token=2']));
  assert.match(commandHash(['a']), /^[0-9a-f]{64}$/);
});

test('the API and its own parents are never listed, whatever they run', async () => {
  const src = fake([{ pid: 1, ppid: 0, argv: ['init'] }, { pid: 450, ppid: 1, argv: ['bash'] }, { pid: 500, ppid: 450, argv: ['tsx', 'watch', 'src/server.ts'], cwd: '/tmp/wt/api' }],
    { 450: [6000], 500: [6001] });
  assert.deepEqual(await listCandidates(src, seats, { codeRoot: ROOT, installPorts: [] }), []);
});

test('helpers: runtimes, supervisors, install paths, /proc parsing', () => {
  assert.ok(isRuntime(['claude']) && isRuntime(['/home/p/.local/bin/codex']) && isRuntime(['node', '/x/@google/gemini-cli/dist/index.js']));
  assert.ok(!isRuntime(['node', 'vite']) && !isRuntime([]));
  assert.equal(supervisorSeat(['python3', '/r/scripts/seat_supervisor.py', 'run', 'gm']), 'gm');
  assert.equal(supervisorSeat(['python3', 'x.py', 'run', 'gm']), null);
  assert.ok(isInstallService({ argv: ['python3', 'services/arturo/arturo-proxy.py'], cwd: ROOT }, ROOT));
  assert.ok(isInstallService({ argv: ['python3', 'services/foo.py'], cwd: ROOT }, ROOT));
  assert.ok(!isInstallService({ argv: ['python3', 'services/foo.py'], cwd: '/tmp/other' }, ROOT));
  assert.ok(!isInstallService({ argv: ['node', `${ROOT}-copy/api/dist/server.js`], cwd: '/' }, ROOT));
  const tcp = '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n'
    + '   0: 0100007F:22B8 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0\n'
    + '   1: 0100007F:1F90 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1000        0 12346 1 0000000000000000 20 4 30 10 -1\n';
  assert.deepEqual([...parseNetTcp(tcp)], [['12345', 8888]]);
  assert.deepEqual(parseStat('42 (my (odd) prog) S 7 42 42 0 -1 4194304 1 0 0 0 0 0 0 0 20 0 1 0 98765 1 1'), { state: 'S', ppid: 7, startTicks: 98765 });
});

function stopFixture() {
  const src = fake([...base, { pid: 100, ppid: 22, argv: ['node', 'vite', '--token=abc'] }], { 100: [5173] }, { 20: 'dev-a' });
  const sent: [number, string][] = [];
  return { src, sent, kill: (pid: number, sig: NodeJS.Signals) => { sent.push([pid, sig]); } };
}
// The fake's clock reads tick 4000 (/proc/uptime) when the stop begins.
const fast = { sleep: async () => {}, graceMs: 1000, pollMs: 250, settleMs: 0 };
const HASH = commandHash(['node', 'vite', '--token=abc']);

test('stop: refuses a pid not listed, a stale start time (pid reused), another command hash or none; sends nothing', async () => {
  const { src, sent, kill } = stopFixture();
  const r1 = await stopCandidate(src, seats, opts, { pid: 22, startTicks: 1022, commandHash: commandHash(['/bin/bash', '-c', 'npm run dev']) }, { kill, ...fast });
  assert.deepEqual([r1.ok, !r1.ok && r1.status], [false, 409]);
  const r2 = await stopCandidate(src, seats, opts, { pid: 100, startTicks: 999, commandHash: HASH }, { kill, ...fast });
  assert.deepEqual([r2.ok, !r2.ok && r2.status], [false, 409]);
  const r3 = await stopCandidate(src, seats, opts, { pid: 100, startTicks: 1100, commandHash: commandHash(['node', 'vite', '--token=xyz']) }, { kill, ...fast });
  assert.deepEqual([r3.ok, !r3.ok && r3.status], [false, 409]);
  // the masked command is not the hash: a client can't stop by sending what it was shown
  const r4 = await stopCandidate(src, seats, opts, { pid: 100, startTicks: 1100, commandHash: 'node vite --token=***' }, { kill, ...fast });
  assert.deepEqual([r4.ok, !r4.ok && r4.status], [false, 409]);
  const r5 = await stopCandidate(src, seats, opts, { pid: '100', startTicks: 1100, commandHash: HASH }, { kill, ...fast });
  assert.deepEqual([r5.ok, !r5.ok && r5.status], [false, 400]);
  assert.deepEqual(sent, []);
});

test('stop: SIGTERM, and no SIGKILL when it exits within the grace period', async () => {
  const { src, sent } = stopFixture();
  const kill = (pid: number, sig: NodeJS.Signals) => { sent.push([pid, sig]); src.table.delete(pid); };
  const r = await stopCandidate(src, seats, opts, { pid: 100, startTicks: 1100, commandHash: HASH }, { kill, ...fast });
  assert.deepEqual([r.ok, r.ok && r.result, r.ok && r.warning], [true, 'stopped', undefined]);
  assert.equal(r.ok && r.candidate.command, 'node vite --token=***');
  assert.deepEqual(sent, [[100, 'SIGTERM']]);
});

test('stop: SIGKILL after the grace period only while the same process is alive; a reused pid is not killed', async () => {
  const a = stopFixture();
  const killA = (pid: number, sig: NodeJS.Signals) => { a.sent.push([pid, sig]); if (sig === 'SIGKILL') a.src.table.delete(pid); };
  const r = await stopCandidate(a.src, seats, opts, { pid: 100, startTicks: 1100, commandHash: HASH }, { kill: killA, ...fast });
  assert.deepEqual([r.ok, r.ok && r.result], [true, 'killed']);
  assert.deepEqual(a.sent, [[100, 'SIGTERM'], [100, 'SIGKILL']]);

  // It exits on SIGTERM and the pid is taken by another process before the grace period ends: no SIGKILL.
  const b = stopFixture();
  const killB = (pid: number, sig: NodeJS.Signals) => { b.sent.push([pid, sig]); b.src.table.set(pid, { ...b.src.table.get(pid)!, startTicks: 777777 }); b.src.listening.delete(pid); };
  const rb = await stopCandidate(b.src, seats, opts, { pid: 100, startTicks: 1100, commandHash: HASH }, { kill: killB, ...fast });
  assert.deepEqual([rb.ok, rb.ok && rb.result], [true, 'stopped']);
  assert.deepEqual(b.sent, [[100, 'SIGTERM']]);
});

test('stop: something listening on its port again afterwards is reported, naming the port and its parent', async () => {
  const { src, sent } = stopFixture();
  const kill = (pid: number, sig: NodeJS.Signals) => {
    sent.push([pid, sig]);
    src.table.delete(pid);
    // a watcher (npm, pid 22's child) starts a new one on the same port
    src.table.set(150, { pid: 150, ppid: 151, uid: UID, startTicks: 5000, state: 'S', argv: ['node', 'vite'], cwd: '/tmp', cgroup: '', agentEnv: 'dev-a' });
    src.table.set(151, { pid: 151, ppid: 22, uid: UID, startTicks: 4999, state: 'S', argv: ['/usr/bin/node', '/usr/lib/node_modules/nodemon/bin/nodemon.js'], cwd: '/tmp', cgroup: '', agentEnv: 'dev-a' });
    src.listening.set(150, [5173]);
  };
  const r = await stopCandidate(src, seats, opts, { pid: 100, startTicks: 1100, commandHash: HASH }, { kill, ...fast });
  assert.deepEqual([r.ok, r.ok && r.result], [true, 'stopped']);
  assert.equal(r.ok && r.warning, 'Stopped, but something restarted it on :5173 (started by node nodemon.js)');
  assert.equal(restartedOn(src, { pid: 999, startTicks: 1, ports: [1234] }, 4000), null);
});

test('stop: a listener on its port that started BEFORE the stop is not a restart', async () => {
  const { src, sent } = stopFixture();
  // another process already shared the port (started at tick 3000, before the stop at 4000)
  src.table.set(160, { pid: 160, ppid: 22, uid: UID, startTicks: 3000, state: 'S', argv: ['node', 'other'], cwd: '/tmp', cgroup: '', agentEnv: 'dev-a' });
  src.listening.set(160, [5173]);
  const kill = (pid: number, sig: NodeJS.Signals) => { sent.push([pid, sig]); src.table.delete(pid); };
  const r = await stopCandidate(src, seats, opts, { pid: 100, startTicks: 1100, commandHash: HASH }, { kill, ...fast });
  assert.deepEqual([r.ok, r.ok && r.result, r.ok && r.warning], [true, 'stopped', undefined]);
  assert.match(restartedOn(src, { pid: 100, startTicks: 1100, ports: [5173] }, 2999) ?? '', /restarted it on :5173/);
});
