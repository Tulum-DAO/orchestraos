/**
 * Web status lag (Shaw, 2026-10-07: "why does the Quest see status change instantly and the web
 * takes forever?"). gm msg_190daa19 item 2: the API stamped its detector snapshot when the scan
 * FINISHED and busted it only on `newestEvent > at`, so a pane event landing DURING the scan was
 * "older" than the snapshot and ignored until the 15 s TTL.
 *
 * End to end through the REAL module: a fake detector that reads the seat's state at scan START
 * and then takes 1 s (the real one takes 3.8-8.4 s), and a pane event written mid-scan.
 * ORCHESTRA_DIR is read at import time, so it is set BEFORE the dynamic import.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

test('an event that lands DURING a scan triggers a re-scan, instead of waiting out the TTL', async () => {
  const root = mkdtempSync(join(tmpdir(), 'lag-'));
  mkdirSync(join(root, 'scripts'), { recursive: true });
  const panes = join(root, 'state', 'agent-events', 'panes');
  mkdirSync(panes, { recursive: true });
  const stateFile = join(root, 'seat-state');
  writeFileSync(stateFile, 'working');
  // Reads the state at START, then is slow: exactly how a long scan misses a mid-scan change.
  writeFileSync(join(root, 'scripts', 'agent-status.py'),
    `import json,time\ns=open(${JSON.stringify(stateFile)}).read().strip()\ntime.sleep(1.0)\n` +
    `print(json.dumps([{"session":"s1","state":s,"activity":""}]))\n`);
  writeFileSync(join(panes, '1.json'), '{"state":"working"}');
  process.env.ORCHESTRA_DIR = root;
  process.env.ORCHESTRA_SCRIPTS_DIR = join(root, 'scripts');   // public resolves the detector this way
  const m = await import(`./agent-status.js?t=${Date.now()}`);

  // 1. cold scan: working
  await m.getDetectorStates(3000);
  assert.equal((await m.getDetectorStates()).get('s1')?.state, 'working');

  // 2. a new event starts a scan; the seat goes idle WHILE that scan runs
  await sleep(20);
  writeFileSync(join(panes, '1.json'), '{"state":"working","n":2}');
  await m.getDetectorStates();            // kicks off scan #2 (it captures "working")
  await sleep(200);
  writeFileSync(stateFile, 'idle');
  writeFileSync(join(panes, '1.json'), '{"state":"idle"}');   // the Stop event, mid-scan
  // 3. keep polling (as the web does) until idle, with a deadline well inside the 15 s TTL. The
  //    old code stays "working" until the TTL, so it fails deterministically; the fix gets there
  //    one scan later. No fixed sleeps: a loaded CI box only makes this slower, not flaky.
  const deadline = Date.now() + 6000;
  let st: string | undefined;
  while (Date.now() < deadline) {
    st = (await m.getDetectorStates()).get('s1')?.state;
    if (st === 'idle') break;
    await sleep(200);
  }
  assert.equal(st, 'idle', 'the mid-scan Stop event must not wait out the 15 s TTL');
});

test('isStale: TTL, a changed event mtime, and a quiet window', async () => {
  const { isStale } = await import('./agent-status.js');
  assert.equal(isStale({ at: 1000, eventMtime: 900 }, 2000, 900), false, 'quiet + fresh');
  assert.equal(isStale({ at: 1000, eventMtime: 900 }, 2000, 1500), true, 'event the scan did not see');
  assert.equal(isStale({ at: 1000, eventMtime: 900 }, 1000 + 15_001, 900), true, 'TTL');
});

test('a scan SLOWER than the TTL is fresh when it finishes: no back-to-back re-scan', async () => {
  // Review of #199: with `at` stamped at scan START, a scan longer than the TTL was stale the
  // moment it finished, so every poll on a slow host started another full-fleet scan.
  const root = mkdtempSync(join(tmpdir(), 'lag-ttl-'));
  mkdirSync(join(root, 'scripts'), { recursive: true });
  mkdirSync(join(root, 'state', 'agent-events', 'panes'), { recursive: true });
  const counter = join(root, 'runs');
  writeFileSync(join(root, 'scripts', 'agent-status.py'),
    `import json,time\nopen(${JSON.stringify(counter)},'a').write('x')\ntime.sleep(1.0)\n` +
    `print(json.dumps([{"session":"s1","state":"idle","activity":""}]))\n`);
  process.env.ORCHESTRA_DIR = root;
  process.env.ORCHESTRA_SCRIPTS_DIR = join(root, 'scripts');
  process.env.AGENT_STATUS_TTL_MS = '500';          // the scan (1 s) is SLOWER than the TTL
  const m = await import(`./agent-status.js?ttl=${Date.now()}`);
  await m.getDetectorStates(3000);                   // cold call AWAITS scan #1 (~1 s) to completion
  const runs = () => { try { return readFileSync(counter, 'utf-8').length; } catch { return 0; } };
  const before = runs();
  await m.getDetectorStates();                       // polled straight after it finished
  await sleep(200);
  assert.equal(runs(), before, 'a just-finished scan must not be stale on arrival');
  delete process.env.AGENT_STATUS_TTL_MS;
});
