/**
 * userPrefs.ts — a person's own settings ("You" in Settings), wherever they sign in:
 *   <data dir>/state/users/<username>/prefs.json   {"default_chat_style": "modern", …}
 * Keyed by the principal's username (lib/principal.ts). Atomic writes; a missing or bad file reads as no preferences.
 */
import fs from 'fs';
import path from 'path';

const USER_RE = /^[A-Za-z0-9_.@-]{1,120}$/;

export function userPrefsFile(dataDir: string, user: string): string {
  if (!USER_RE.test(user) || user.includes('..')) throw new Error(`bad user name: ${user}`);
  return path.join(dataDir, 'state', 'users', user, 'prefs.json');
}

export function readUserPrefs(dataDir: string, user: string): Record<string, unknown> {
  try {
    const d = JSON.parse(fs.readFileSync(userPrefsFile(dataDir, user), 'utf-8'));
    return d && typeof d === 'object' && !Array.isArray(d) ? d : {};
  } catch { return {}; }
}

/** Set one preference; `null` removes it. */
export function writeUserPref(dataDir: string, user: string, key: string, value: unknown): void {
  const f = userPrefsFile(dataDir, user);
  const cur = readUserPrefs(dataDir, user);
  if (value === null) delete cur[key]; else cur[key] = value;
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(cur, null, 2) + '\n');
  fs.renameSync(tmp, f);
}
