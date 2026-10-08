/**
 * A seat on THIS host is alive when its tmux session is up here, whatever its machine label.
 *
 * Operator report, live new-user test 2026-10-08: on a fresh `orchestra starter` install, gm
 * showed a red dot, sat under "Not running" and offered Spawn, while `tmux ls` showed
 * "gm: 1 windows (attached)". `orchestra` writes `machine: "local"` on every seat it
 * registers (orchestra_cli/seats.py register_seat), but every this-host branch in
 * cross-machine.ts tested `machine === 'vps'`. So getUnifiedAgentStatus fell through to
 * `tmux_alive = false` for every seat on every public install, and that value overrides the
 * route's own correct local check (`unified?.tmux_alive ?? isLocalAlive`). A seat the
 * detector also had no entry for kept alive:false: the Spawn button, on a live session.
 * spawn/kill/inject/capture took the SSH branch for the same label and failed with
 * "Unknown machine: local".
 *
 * By effect: a real tmux server on a private socket dir, so nothing touches a live fleet.
 *
 * Run: cd api && npx tsx --test src/services/cross-machine.thishost.test.ts
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'child_process';
import { mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

const sock = mkdtempSync(join(tmpdir(), 'thishost-tmux-'));
const data = mkdtempSync(join(tmpdir(), 'thishost-data-'));
process.env.TMUX_TMPDIR = sock;      // every tmux the module runs talks to OUR server
delete process.env.TMUX;             // a test run from inside tmux must not reach that server
process.env.ORCHESTRA_DIR = data;

const tmux = (...a: string[]) => execFileSync('tmux', a, { env: process.env, encoding: 'utf-8' });

let cm: typeof import('./cross-machine.js');

before(async () => {
  tmux('new-session', '-d', '-s', 'gm', 'sleep 300');
  cm = await import('./cross-machine.js');
});

after(() => {
  try { tmux('kill-server'); } catch { /* already gone */ }
  rmSync(sock, { recursive: true, force: true });
  rmSync(data, { recursive: true, force: true });
});

test('isThisHost: the labels a single-machine install writes', () => {
  for (const m of [undefined, '', 'vps', 'local']) assert.equal(cm.isThisHost(m), true, String(m));
  for (const m of ['mac', 'kai-laptop', 'unknown']) assert.equal(cm.isThisHost(m), false, m);
});

test('a machine:"local" seat whose session is up here is alive', async () => {
  const st = await cm.getUnifiedAgentStatus({ agents: { gm: { machine: 'local', tmux_session: 'gm' } } });
  assert.equal(st.gm.tmux_alive, true);
  assert.equal(st.gm.machine_status, 'online');
});

test('a machine:"local" seat with no session is not alive', async () => {
  const st = await cm.getUnifiedAgentStatus({ agents: { 'dev-x': { machine: 'local' } } });
  assert.equal(st['dev-x'].tmux_alive, false);
});

test('a session up here beats any label (liveness-pre-union rule)', async () => {
  const st = await cm.getUnifiedAgentStatus({ agents: { gm: { machine: 'mac', tmux_session: 'gm' } } });
  assert.equal(st.gm.tmux_alive, true);
});

test('capture on a machine:"local" seat reads the local pane, not SSH', async () => {
  const r = await cm.captureRemoteOutput('gm', 5, { agents: { gm: { machine: 'local', tmux_session: 'gm' } } });
  assert.equal(r.success, true, r.output);
});

test('paneCommand names what a session\'s screen is running, exact name only', async () => {
  const { paneCommand } = await import('./tmux-monitor.js');
  assert.equal(paneCommand('gm'), 'sleep');
  assert.equal(paneCommand('g'), null, 'a prefix must not resolve to gm');
  assert.equal(paneCommand('no-such-seat'), null);
});
