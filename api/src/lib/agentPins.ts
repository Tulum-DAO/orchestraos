/**
 * agentPins.ts — the agents each person pinned to the top of the sidebar, saved on the server per person
 * (state/agent-pins.json: {"users": {"<username>": ["<agent id>", ...]}}), so pins follow you across devices the
 * way saved looks do. Keyed by username, ready for more than one person per install. Atomic tmp-replace writes;
 * a missing or bad file reads as no pins.
 */
import fs from 'fs';
import path from 'path';

const MAX_PINS = 24;
const ID_RE = /^[A-Za-z0-9_.:-]{1,80}$/;

function file(orchestraDir: string): string {
  return path.join(orchestraDir, 'state', 'agent-pins.json');
}

function readAll(orchestraDir: string): { users: Record<string, string[]> } {
  try {
    const d = JSON.parse(fs.readFileSync(file(orchestraDir), 'utf8'));
    return d && typeof d === 'object' && d.users && typeof d.users === 'object' ? d : { users: {} };
  } catch { return { users: {} }; }
}

/** A clean pin list (ids only, no repeats, at most 24), or null when it is not a list. */
export function cleanPins(raw: unknown): string[] | null {
  if (!Array.isArray(raw)) return null;
  const out: string[] = [];
  for (const v of raw) if (typeof v === 'string' && ID_RE.test(v) && !out.includes(v)) out.push(v);
  return out.slice(0, MAX_PINS);
}

export function readPins(orchestraDir: string, username: string): string[] {
  return cleanPins(readAll(orchestraDir).users[username]) || [];
}

export function writePins(orchestraDir: string, username: string, pins: string[]): void {
  const all = readAll(orchestraDir);
  all.users[username] = pins;
  const f = file(orchestraDir);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(all));
  fs.renameSync(tmp, f);
}
