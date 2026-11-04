/**
 * voiceLimits.ts — the install's daily voice limit (Settings › Voice & calls), in
 *   <data dir>/state/voice-limits.json   {"daily_cap_min": 120}
 * services/arturo/voice_usage.py reads it on every check (no restart); VOICE_DAILY_CAP_MIN stays its fallback.
 */
import fs from 'fs';
import path from 'path';

export const DEFAULT_DAILY_LIMIT = 120;
export const DAILY_LIMIT_MAX = 1440;
const limitsFile = (dataDir: string) => path.join(dataDir, 'state', 'voice-limits.json');

export function validDailyLimit(v: unknown): v is number {
  return typeof v === 'number' && Number.isInteger(v) && v >= 1 && v <= DAILY_LIMIT_MAX;
}

export function readVoiceLimit(dataDir: string): number | null {
  try {
    const v = JSON.parse(fs.readFileSync(limitsFile(dataDir), 'utf-8'))?.daily_cap_min;
    return validDailyLimit(v) ? v : null;
  } catch { return null; }
}

export function writeVoiceLimit(dataDir: string, minutes: number): void {
  const f = limitsFile(dataDir);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ daily_cap_min: minutes }) + '\n');
  fs.renameSync(tmp, f);
}
