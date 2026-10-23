import { timingSafeEqual } from 'crypto';
import { readFileSync } from 'fs';
import { join } from 'path';
import { loadConfig } from './config.js';

const HEADER = 'x-orchestra-terminal-relay';

function secret(): string {
  const p = process.env.TERMINAL_RELAY_SECRET_FILE || join(loadConfig().dataDir, 'state', 'terminal-relay-secret');
  try { return readFileSync(p, 'utf8').trim(); } catch { return ''; }
}

export function isTerminalRelay(req: { headers: Record<string, string | string[] | undefined> }): boolean {
  const got = req.headers[HEADER];
  const value = Array.isArray(got) ? got[0] : got;
  const expected = secret();
  if (!value || !expected || value.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(value), Buffer.from(expected));
}
