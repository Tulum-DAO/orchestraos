/**
 * The task prompt tells a seat where to PATCH its task: the configured [api] port, never a
 * literal 8888 (pm doc test 2026-10-08: with a changed port, seats PATCHed another app).
 * Run: cd api && npx tsx --test src/routes/tasks.apibase.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { apiBaseForSeats } from './tasks.js';

test('a configured api port reaches the seat prompt', () => {
  assert.equal(apiBaseForSeats({ ORCHESTRA_API_PORT: '18888' }), 'http://127.0.0.1:18888');
  assert.equal(apiBaseForSeats({ PORT: '18889' }), 'http://127.0.0.1:18889');
  assert.equal(apiBaseForSeats({ ORCHESTRA_API_PORT: '', PORT: '' }), 'http://127.0.0.1:8888');
  assert.equal(apiBaseForSeats({}), 'http://127.0.0.1:8888');
});
