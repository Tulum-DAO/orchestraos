import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, appendFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { appendSettingChange, lastChanges } from './settingsLog.js';

test('the latest change per key wins; a bad line is skipped; no file is no changes', () => {
  const d = mkdtempSync(join(tmpdir(), 'settings-log-'));
  assert.equal(lastChanges(d).size, 0);
  appendSettingChange(d, { key: 'general.timeZone', scope: 'everyone', by: 'sam@x.net', at: '2026-11-03T10:00:00Z', value: 'UTC' });
  mkdirSync(join(d, 'state'), { recursive: true });
  appendFileSync(join(d, 'state', 'settings-log.jsonl'), '{not json\n');
  appendSettingChange(d, { key: 'general.timeZone', scope: 'everyone', by: 'mo@y.net', at: '2026-11-03T10:05:00Z', value: 'Asia/Tokyo' });
  appendSettingChange(d, { key: 'voice.dailyLimitMin', scope: 'everyone', by: 'sam@x.net', at: '2026-11-03T10:06:00Z', value: 60 });
  const m = lastChanges(d);
  assert.deepEqual(m.get('general.timeZone'), { by: 'mo@y.net', at: '2026-11-03T10:05:00Z' });
  assert.deepEqual(m.get('voice.dailyLimitMin'), { by: 'sam@x.net', at: '2026-11-03T10:06:00Z' });
});
