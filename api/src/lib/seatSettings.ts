/**
 * seatSettings.ts — a seat's rotation settings, the API's twin of scripts/seat_settings.py (ONE reader each side,
 * the same fallback order): `auto_compact` (the main switch: our compaction runs on its own only while it is on;
 * default off), `compact_at` (% of the context window, 40–85; kept while the switch is off, applied only while it is
 * on), `show_compaction` (whether the chat shows the compaction notice). Per AGENT: every person sees the same (a
 * per-person override comes later with the fleet-wide menu). `guard_pct` (90) is the hard guard, never from a file.
 *
 *   <data dir>/state/seat-settings.json          {"seats": {"<id>": {...}}, "history": [{seat, field, old, new, by, at}]}
 *   <data dir>/state/seat-settings-default.json  {"default": {...}}  (the fleet default; optional)
 *
 * Fallback per field: the seat's value, else the fleet default, else built in. A stored value outside its range or of
 * the wrong type is ignored, never clamped. Writes are atomic and the file is the owner's only (0600).
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

export const BUILTIN = { auto_compact: false, show_compaction: true, compact_at: 65 } as const;
export const GUARD_PCT = 90;
export const COMPACT_MIN = 40;
export const COMPACT_MAX = 85;
export const FIELDS = ['auto_compact', 'show_compaction', 'compact_at'] as const;
export type SettingField = typeof FIELDS[number];
export type Source = 'seat' | 'default' | 'builtin';

export interface SeatSettings {
  auto_compact: boolean;
  show_compaction: boolean;
  compact_at: number;
  guard_pct: number;
  source: Record<SettingField, Source>;
}

export interface HistoryRow { seat: string; field: SettingField; old: unknown; new: unknown; by: string | null; at: string }

const settingsFile = (dataDir: string) => path.join(dataDir, 'state', 'seat-settings.json');
const defaultFile = (dataDir: string) => path.join(dataDir, 'state', 'seat-settings-default.json');

/** Is this a value the field may hold? (A boolean is not a number here: true is not 1 %.) */
export function validSetting(field: string, v: unknown): boolean {
  if (field === 'auto_compact' || field === 'show_compaction') return typeof v === 'boolean';
  if (field === 'compact_at') return typeof v === 'number' && Number.isInteger(v) && v >= COMPACT_MIN && v <= COMPACT_MAX;
  return false;
}

function readJson(p: string): Record<string, any> {
  try {
    const v = JSON.parse(fs.readFileSync(p, 'utf-8'));
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch { return {}; }
}

export function readSeatSettings(dataDir: string, seat: string): SeatSettings {
  const seats = readJson(settingsFile(dataDir)).seats;
  const mine = seats && typeof seats === 'object' && seats[seat] && typeof seats[seat] === 'object' ? seats[seat] : {};
  const fleetRaw = readJson(defaultFile(dataDir)).default;
  const fleet = fleetRaw && typeof fleetRaw === 'object' ? fleetRaw : {};
  const out: any = { guard_pct: GUARD_PCT, source: {} };
  for (const f of FIELDS) {
    if (f in mine && validSetting(f, mine[f])) { out[f] = mine[f]; out.source[f] = 'seat'; }
    else if (f in fleet && validSetting(f, fleet[f])) { out[f] = fleet[f]; out.source[f] = 'default'; }
    else { out[f] = BUILTIN[f]; out.source[f] = 'builtin'; }
  }
  return out as SeatSettings;
}

/** A change from the API, checked: {change} or {error} naming the first bad field (an empty change is an error). */
export function cleanSettingsChange(body: unknown): { change: Partial<Record<SettingField, boolean | number>> } | { error: string } {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { error: 'a JSON object is required' };
  const change: Partial<Record<SettingField, boolean | number>> = {};
  for (const [k, v] of Object.entries(body as Record<string, unknown>)) {
    if (!(FIELDS as readonly string[]).includes(k)) return { error: `${k}: not a setting` };
    if (!validSetting(k, v)) {
      return { error: k === 'compact_at' ? `compact_at: a whole number from ${COMPACT_MIN} to ${COMPACT_MAX}` : `${k}: true or false` };
    }
    change[k as SettingField] = v as boolean | number;
  }
  if (!Object.keys(change).length) return { error: 'nothing to change' };
  return { change };
}

/** Each seat's own valid values (the fields readSeatSettings reads as source 'seat'), by seat id. Settings › Agents
 *  counts these as "uses its own" (lib/agentOverrides.ts). */
export function readSeatOverrides(dataDir: string): Record<string, Partial<Record<SettingField, boolean | number>>> {
  const seats = readJson(settingsFile(dataDir)).seats;
  const out: Record<string, Partial<Record<SettingField, boolean | number>>> = {};
  if (!seats || typeof seats !== 'object' || Array.isArray(seats)) return out;
  for (const [id, row] of Object.entries(seats as Record<string, unknown>)) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue;
    const own: Partial<Record<SettingField, boolean | number>> = {};
    for (const f of FIELDS) if (f in row && validSetting(f, (row as Record<string, unknown>)[f])) own[f] = (row as Record<string, boolean | number>)[f];
    if (Object.keys(own).length) out[id] = own;
  }
  return out;
}

/** Apply a checked change for one seat: one history row per field whose value changes, by the person who changed it. */
export function writeSeatSettings(dataDir: string, seat: string, change: Partial<Record<SettingField, boolean | number>>,
                                  by: string | null, now: () => Date = () => new Date()): SeatSettings {
  const f = settingsFile(dataDir);
  const doc = readJson(f);
  const seats: Record<string, Record<string, unknown>> = doc.seats && typeof doc.seats === 'object' ? doc.seats : {};
  const history: HistoryRow[] = Array.isArray(doc.history) ? doc.history : [];
  const before = readSeatSettings(dataDir, seat);
  const cur: Record<string, unknown> = { ...(seats[seat] || {}) };
  const at = now().toISOString().replace(/\.\d{3}Z$/, 'Z');
  for (const [k, v] of Object.entries(change) as [SettingField, boolean | number][]) {
    if (k in cur && cur[k] === v) continue;
    history.push({ seat, field: k, old: before[k], new: v, by, at });
    cur[k] = v;
  }
  seats[seat] = cur;
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ ...doc, seats, history }, null, 2) + '\n', { mode: 0o600 });
  fs.chmodSync(tmp, 0o600);
  fs.renameSync(tmp, f);
  return readSeatSettings(dataDir, seat);
}

export function seatHistory(dataDir: string, seat: string): HistoryRow[] {
  const h = readJson(settingsFile(dataDir)).history;
  return (Array.isArray(h) ? h : []).filter((r: any) => r && r.seat === seat);
}

/** Runtimes our rotation can drive today: a headless-capable runtime with a built, tested adapter
 *  (scripts/headless_switch.py HEADLESS_RUNTIMES; scripts/rotation_adapters). Others refuse until theirs exists. */
export const ROTATING_RUNTIMES: ReadonlySet<string> = new Set(['claude']);

/** The /compact gate both sides read: contract/compact-gate.json at the checkout root, found from this module so it is
 *  the same file from src/ under tsx and from dist/ in production. */
export const COMPACT_GATE_FILE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'contract', 'compact-gate.json');

export interface CompactGate { needs_headless: boolean; needs_test_seat: boolean }

/** The gate's two flags, or null when the file is missing, unreadable or a flag is not a boolean: then nothing is
 *  offered, the same as the gateway, which refuses when it cannot read the gate. */
export function readCompactGate(file: string = COMPACT_GATE_FILE): CompactGate | null {
  try {
    const doc = JSON.parse(fs.readFileSync(file, 'utf-8'));
    if (typeof doc?.needs_headless !== 'boolean' || typeof doc?.needs_test_seat !== 'boolean') return null;
    return { needs_headless: doc.needs_headless, needs_test_seat: doc.needs_test_seat };
  } catch { return null; }
}

/** Whether a person's "/compact" can run for this seat now: the gateway's own gate (scripts/watch_gateway.py
 *  _compact_allowed), with its flags from contract/compact-gate.json, whose cases both sides run. Headless means
 *  switched on and NOT in Dev: in Dev the seat's TUI runs in the pane, the gateway reads it as not headless and
 *  refuses, so this must not offer it. The slideout reads it to offer "Compact now" only where it can work; the
 *  gateway still decides. */
export function seatCanCompactNow(agentId: string, registry: { agents?: Record<string, { test?: unknown; tmux_session?: unknown }> } | null,
  headless: Set<string>, inDev: (id: string) => boolean = () => false,
  gate: CompactGate | null = readCompactGate()): boolean {
  if (!gate) return false;
  if (gate.needs_headless && (!headless.has(agentId) || inDev(agentId))) return false;
  if (!gate.needs_test_seat) return true;
  const agents = registry?.agents ?? {};
  for (const [name, row] of Object.entries(agents)) {
    if (row && typeof row === 'object' && row.test === true && (name === agentId || row.tmux_session === agentId)) return true;
  }
  return false;
}

/** Can this seat use our rotation? A registered seat on a rotating runtime; never Arturo (he is not a seat). A seat
 *  row with no runtime is read as claude, the only runtime seats were spawned on before the field existed. */
export function seatRotates(agentId: string, registry: { agents?: Record<string, { runtime?: unknown }> } | null): boolean {
  if (agentId === 'arturo') return false;
  const row = registry?.agents?.[agentId];
  if (!row || typeof row !== 'object') return false;
  const rt = typeof row.runtime === 'string' && row.runtime ? row.runtime.toLowerCase() : 'claude';
  return ROTATING_RUNTIMES.has(rt);
}

/** The install default's valid fields (seat-settings-default.json {"default": {...}}). */
export function readInstallDefault(dataDir: string): Partial<Record<SettingField, boolean | number>> {
  const raw = readJson(defaultFile(dataDir)).default;
  const out: Partial<Record<SettingField, boolean | number>> = {};
  if (!raw || typeof raw !== 'object') return out;
  for (const f of FIELDS) if (f in raw && validSetting(f, raw[f])) out[f] = raw[f];
  return out;
}

/** Settings › Agents: change the install default (seats with their own value keep it). Atomic, owner-only. */
export function writeInstallDefault(dataDir: string, change: Partial<Record<SettingField, boolean | number>>): void {
  for (const [k, v] of Object.entries(change)) if (!validSetting(k, v)) throw new Error(`${k}: not a valid value`);
  const f = defaultFile(dataDir);
  const doc = readJson(f);
  // Keys under "default" this version doesn't know (a newer field, a note) are kept: spread what is there first.
  const prev = doc.default && typeof doc.default === 'object' && !Array.isArray(doc.default) ? doc.default : {};
  const next = { ...doc, default: { ...prev, ...change } };
  fs.mkdirSync(path.dirname(f), { recursive: true });
  const tmp = `${f}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(next, null, 2) + '\n', { mode: 0o600 });
  fs.chmodSync(tmp, 0o600);
  fs.renameSync(tmp, f);
}
