/**
 * B1 outsider finding 4 (gm msg_d69910cc) — RED-first.
 *
 * services/agent-status.ts built DETECTOR = <ORCHESTRA_DIR>/scripts/agent-status.py. Under
 * `orchestra up`, ORCHESTRA_DIR is the DATA dir (~/.orchestra), so every execFile ENOENTed,
 * the cache never filled, and /api/agents reported every seat alive:false status:unknown
 * detector_age_ms:-1 forever on any machine where data dir != checkout. Code paths resolve
 * from ORCHESTRA_SCRIPTS_DIR / ORCHESTRA_ROOT / this file's own location — never the data dir.
 *
 * Run: cd api && npx tsx --test tests/agent-status-detector-path.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveDetectorPath } from '../src/services/agent-status.js';

const HERE = 'file:///repo/api/dist/services/agent-status.js';

test('ORCHESTRA_SCRIPTS_DIR wins', () => {
  const p = resolveDetectorPath({ ORCHESTRA_SCRIPTS_DIR: '/code/scripts', ORCHESTRA_DIR: '/data', ORCHESTRA_ROOT: '/other' }, HERE);
  assert.equal(p, '/code/scripts/agent-status.py');
});

test('ORCHESTRA_ROOT next', () => {
  const p = resolveDetectorPath({ ORCHESTRA_ROOT: '/code', ORCHESTRA_DIR: '/data' }, HERE);
  assert.equal(p, '/code/scripts/agent-status.py');
});

test('falls back to the checkout this module lives in — never the data dir', () => {
  const p = resolveDetectorPath({ ORCHESTRA_DIR: '/data' }, HERE);
  assert.equal(p, '/repo/scripts/agent-status.py');
  assert.ok(!p.startsWith('/data'));
});
