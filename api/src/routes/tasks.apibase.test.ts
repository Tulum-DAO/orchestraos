/**
 * The task prompt tells a seat where to PATCH its task: the configured [api] port, never a
 * literal 8888 (pm doc test 2026-10-08: with a changed port, seats PATCHed another app).
 * Run: cd api && npx tsx --test src/routes/tasks.apibase.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { apiBaseForSeats } from './tasks.js';

test('a configured api port reaches the seat prompt', () => {
  const cfg = () => 8888;
  assert.equal(apiBaseForSeats({ ORCHESTRA_API_PORT: '18888' }, cfg), 'http://127.0.0.1:18888');
  assert.equal(apiBaseForSeats({ PORT: '18889' }, cfg), 'http://127.0.0.1:18889');
  assert.equal(apiBaseForSeats({}, cfg), 'http://127.0.0.1:8888');
  assert.equal(apiBaseForSeats({}, () => 18890), 'http://127.0.0.1:18890', 'the toml port, not a literal');
});

test("server.ts's precedence: PORT before ORCHESTRA_API_PORT", () => {
  assert.equal(apiBaseForSeats({ PORT: '1', ORCHESTRA_API_PORT: '2' }, () => 3), 'http://127.0.0.1:1');
});

test('ORCH_API_URL wins: it carries a configured [api] host', () => {
  assert.equal(apiBaseForSeats({ ORCH_API_URL: 'http://100.64.0.5:18888/', PORT: '1' }, () => 3),
               'http://100.64.0.5:18888');
});
