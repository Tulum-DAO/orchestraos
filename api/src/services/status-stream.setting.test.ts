/**
 * An Everyone setting change reaches every open dashboard on GET /api/status/stream as `event: setting`.
 * Run: npx tsx --test src/services/status-stream.setting.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import type { Response } from 'express';

const dir = testTmpDir('status-setting-');
process.env.REALTIME_STATUS_FILE = join(dir, 'status.json');
const { addStatusSSEClient, broadcastSetting, getStatusSSEClientCount } = await import('./status-stream.js');

test('a setting change is one `event: setting` frame to every client; a closed client is dropped', () => {
  const frames: string[] = [];
  const handlers: Record<string, () => void> = {};
  const res = { write: (s: string) => { frames.push(s); return true; }, on: (ev: string, fn: () => void) => { handlers[ev] = fn; return res; } } as unknown as Response;
  addStatusSSEClient(res);
  const change = { key: 'general.timeZone', value: 'Asia/Tokyo', changedBy: 'sam@x.net', changedAt: '2026-11-03T10:00:00Z' };
  broadcastSetting(change);
  const f = frames.filter((x) => x.startsWith('event: setting\n'));
  assert.equal(f.length, 1);
  assert.deepEqual(JSON.parse(/^data: (.*)$/m.exec(f[0])![1]), change);
  handlers.close();
  assert.equal(getStatusSSEClientCount(), 0);
});
