import { readFileSync } from 'fs';
import path from 'path';

/**
 * The operator's Telegram chat id from a file in the data dir. New first: the Telegram plugin's own
 * record (<data>/state/telegram/chat-id, written when the operator first messages the bot). The old
 * operator-named file (.shaw_chat_id) is still read so existing installs keep working.
 */
export const CHAT_ID_FILES = [path.join('state', 'telegram', 'chat-id'), '.shaw_chat_id'];

export function chatIdFromFiles(dataDir: string): string | null {
  for (const rel of CHAT_ID_FILES) {
    try {
      const v = readFileSync(path.join(dataDir, rel), 'utf-8').trim();
      if (v) return v;
    } catch {}
  }
  return null;
}
