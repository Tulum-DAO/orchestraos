/**
 * The Telegram chat id file in the data dir: the plugin's own record first, the old operator-named
 * file still read (gm). Run: npx tsx --test src/lib/telegramChatId.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { chatIdFromFiles } from './telegramChatId.js';

function dataDir(files: Record<string, string>): string {
  const d = mkdtempSync(join(tmpdir(), 'tgchat-'));
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(join(d, rel, '..'), { recursive: true });
    writeFileSync(join(d, rel), text);
  }
  return d;
}

test('an existing install with only the old file keeps working', () => {
  const d = dataDir({ '.shaw_chat_id': '111\n' });
  try { assert.equal(chatIdFromFiles(d), '111'); } finally { rmSync(d, { recursive: true }); }
});

test("the plugin's record wins over the old file", () => {
  const d = dataDir({ 'state/telegram/chat-id': '222\n', '.shaw_chat_id': '111' });
  try { assert.equal(chatIdFromFiles(d), '222'); } finally { rmSync(d, { recursive: true }); }
});

test('an empty new file falls through; nothing set is null', () => {
  const d = dataDir({ 'state/telegram/chat-id': '\n', '.shaw_chat_id': '111' });
  const e = dataDir({});
  try {
    assert.equal(chatIdFromFiles(d), '111');
    assert.equal(chatIdFromFiles(e), null);
  } finally { rmSync(d, { recursive: true }); rmSync(e, { recursive: true }); }
});
