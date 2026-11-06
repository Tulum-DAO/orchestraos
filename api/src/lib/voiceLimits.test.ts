import { testTmpDir } from './testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { readVoiceLimit, writeVoiceLimit, validDailyLimit, DEFAULT_DAILY_LIMIT } from './voiceLimits.js';

test('the daily limit round-trips; none or a bad value reads as null', () => {
  const d = testTmpDir('voice-limits-');
  assert.equal(readVoiceLimit(d), null);
  writeVoiceLimit(d, 45);
  assert.equal(readVoiceLimit(d), 45);
  mkdirSync(join(d, 'state'), { recursive: true });
  writeFileSync(join(d, 'state', 'voice-limits.json'), '{"daily_cap_min": "lots"}');
  assert.equal(readVoiceLimit(d), null);
  assert.equal(DEFAULT_DAILY_LIMIT, 120);
});

test('a limit is a whole number of minutes, 1 to 1440', () => {
  for (const ok of [1, 120, 1440]) assert.equal(validDailyLimit(ok), true);
  for (const bad of [0, 1441, 2.5, '60', true, null]) assert.equal(validDailyLimit(bad), false, String(bad));
});
