/**
 * model-catalog.ts — the LIVE, login-specific model catalog.
 *
 * The picker must show every model THIS operator's login can use. A
 * hand-written list in config/providers.json drifts from the CLI (and hid two
 * models the operator could actually pick), so each provider may declare a
 * `model_catalog.probe` instead.
 *
 * Like `auth_probe`, a probe is a STRATEGY, not an identity: this module
 * dispatches on `probe.kind` and carries ZERO provider literals. Adding a
 * fourth provider is a providers.json edit, never a code change.
 *
 * Probing NEVER empties the picker: any failure falls back to the static list
 * and says why (`source: 'static-fallback'`, `reason`), in the same
 * fail-LOUD spirit as the auth probe's `unverified`.
 *
 * Nothing here scrapes a `/model` TUI: a stray keystroke in that picker
 * changes the default model for every session on the machine.
 */
import { execFileSync } from 'node:child_process';
import type { ModelCapabilities, ModelInfo, StaticModel } from './runtimes-available.js';

/** A cursor the CLI keeps handing back must not spin forever. */
const MAX_PAGES = 20;
const DEFAULT_TIMEOUT_MS = 20_000;
const DEFAULT_LINGER_MS = 2_000;

/** Single-quote for `sh -c`; the payload itself travels in the environment. */
function shQuote(s: string): string {
  return `'${s.replace(/'/g, `'\''`)}'`;
}

export interface ModelProbeConfig {
  kind: 'tsv-stdout' | 'stdin-json-stream' | 'jsonrpc-stdio';
  /** Whole command line, split on spaces (same convention as auth_probe.cmd). */
  cmd: string;
  timeout_ms?: number;

  // tsv-stdout: tab-separated `id<TAB>label`; non-conforming lines (CLI
  // preambles such as "Fetching available models...") are skipped.
  id_field?: number;
  label_field?: number;

  // stdin-json-stream: write `stdin`, then read the one output line whose
  // shallow keys equal `match`.
  stdin?: string;
  match?: Record<string, string>;

  // jsonrpc-stdio: initialize -> initialized -> `method`, paginated.
  method?: string;
  cursor_path?: string;
  /** How long to hold stdin OPEN after writing. codex app-server exits on EOF
   *  before it answers, so a one-shot write-then-close reads as an empty
   *  catalog (found live, 2026-09-29). */
  linger_ms?: number;

  // shared shape description
  list_path?: string;
  id_key?: string;
  label_key?: string;
  /** Key holding the model an id RESOLVES to (claude: `resolvedModel`). Ids that resolve
   *  to the same model are one row, and a concrete id beats an alias. */
  resolved_key?: string;
  /** The CLI's own "use my default" POINTER, which is not a model: the picker already
   *  offers the default as its empty-model row. */
  default_id?: string;
  hidden_key?: string;
  modalities_key?: string;
}

export type ProbeExec = (
  bin: string,
  args: string[],
  opts: { input?: string; timeoutMs: number; lingerMs?: number },
) => string;

export interface CatalogResult {
  /** What the picker OFFERS: one row per model, pointers and duplicate aliases removed. */
  models: ModelInfo[];
  /** What validation ACCEPTS: the offered ids PLUS the ids that were collapsed away.
   *  A pointer like `default` is a real, valid --model argument, and an operator whose
   *  saved pick predates the collapse must not start getting `unknown_model`. */
  valid_ids: string[];
  source: 'probe' | 'static' | 'static-fallback';
  reason?: string;
}

export const defaultProbeExec: ProbeExec = (bin, args, opts) => {
  if (opts.lingerMs) {
    // `{ cat; sleep N; }` keeps the write end of the pipe open after the
    // payload is written, so a server that waits for more input still answers.
    const cmd = [bin, ...args].map(shQuote).join(' ');
    const seconds = Math.max(1, Math.ceil(opts.lingerMs / 1000));
    return execFileSync(
      'sh',
      ['-c', `printf '%s' "$ORCHESTRAOS_PROBE_INPUT" | { cat; sleep ${seconds}; } | ${cmd}`],
      {
        env: { ...process.env, ORCHESTRAOS_PROBE_INPUT: opts.input || '' },
        timeout: Math.max(opts.timeoutMs, opts.lingerMs + 5_000),
        stdio: ['ignore', 'pipe', 'ignore'],
        maxBuffer: 8 * 1024 * 1024,
      },
    ).toString();
  }
  return execFileSync(bin, args, {
    input: opts.input,
    timeout: opts.timeoutMs,
    stdio: ['pipe', 'pipe', 'ignore'],
    maxBuffer: 8 * 1024 * 1024,
  }).toString();
};

/** What we know about a model we have only seen in a live list. */
const UNKNOWN_CAPABILITIES: ModelCapabilities = {
  text: true, image: false, audio: false, video: false, context_window: null,
};
const UNKNOWN_FIELDS = ['image', 'audio', 'video', 'context_window'];

function getByPath(obj: unknown, dotted: string): unknown {
  return dotted.split('.').reduce<unknown>((acc, key) => {
    if (acc && typeof acc === 'object' && key in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj);
}

function jsonLines(stdout: string): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  for (const line of stdout.split('\n')) {
    const t = line.trim();
    if (!t.startsWith('{')) continue;
    try { out.push(JSON.parse(t) as Record<string, unknown>); } catch { /* partial/ANSI line */ }
  }
  return out;
}

interface RawModel { id: string; label?: string; modalities?: string[]; resolved?: string }

function readRows(rows: unknown, cfg: ModelProbeConfig): RawModel[] {
  if (!Array.isArray(rows)) return [];
  const out: RawModel[] = [];
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const r = row as Record<string, unknown>;
    if (cfg.hidden_key && r[cfg.hidden_key]) continue;
    const id = r[cfg.id_key || 'id'];
    if (typeof id !== 'string' || !id) continue;
    const label = r[cfg.label_key || 'label'];
    const modalities = cfg.modalities_key ? r[cfg.modalities_key] : undefined;
    const resolved = cfg.resolved_key ? r[cfg.resolved_key] : undefined;
    out.push({
      id,
      label: typeof label === 'string' ? label : undefined,
      modalities: Array.isArray(modalities) ? modalities.filter((m): m is string => typeof m === 'string') : undefined,
      resolved: typeof resolved === 'string' && resolved ? resolved : undefined,
    });
  }
  return out;
}

function splitCmd(cmd: string): [string, string[]] {
  const [bin, ...args] = cmd.trim().split(/\s+/);
  return [bin, args];
}

/** Each kind returns the raw rows, or throws; pagination notes go in `notes`. */
function runProbe(cfg: ModelProbeConfig, exec: ProbeExec, notes: string[]): RawModel[] {
  const [bin, args] = splitCmd(cfg.cmd);
  const timeoutMs = cfg.timeout_ms || DEFAULT_TIMEOUT_MS;

  if (cfg.kind === 'tsv-stdout') {
    const stdout = exec(bin, args, { timeoutMs });
    const idField = cfg.id_field ?? 0;
    const labelField = cfg.label_field ?? 1;
    const rows: RawModel[] = [];
    for (const line of stdout.split('\n')) {
      if (!line.includes('\t')) continue; // CLI preamble / blank
      const fields = line.split('\t').map((f) => f.trim());
      const id = fields[idField];
      if (!id) continue;
      rows.push({ id, label: fields[labelField] || undefined });
    }
    return rows;
  }

  if (cfg.kind === 'stdin-json-stream') {
    const stdout = exec(bin, args, { input: cfg.stdin ? `${cfg.stdin}\n` : undefined, timeoutMs });
    const match = cfg.match || {};
    for (const obj of jsonLines(stdout)) {
      const hit = Object.entries(match).every(([k, v]) => obj[k] === v);
      if (!hit) continue;
      return readRows(getByPath(obj, cfg.list_path || 'models'), cfg);
    }
    return [];
  }

  if (cfg.kind === 'jsonrpc-stdio') {
    // One spawn per page: the cursor is only known once the previous page is
    // read, and the route is synchronous by contract.
    const rows: RawModel[] = [];
    const seenCursors = new Set<string>();
    let cursor: string | null = null;
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const params = cursor ? { cursor } : {};
      const input = [
        JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { clientInfo: { name: 'orchestraos', title: 'OrchestraOS', version: '1' } } }),
        JSON.stringify({ jsonrpc: '2.0', method: 'initialized', params: {} }),
        JSON.stringify({ jsonrpc: '2.0', id: 2, method: cfg.method || 'model/list', params }),
        '',
      ].join('\n');
      const stdout = exec(bin, args, {
        input,
        timeoutMs,
        lingerMs: cfg.linger_ms ?? DEFAULT_LINGER_MS,
      });
      const reply = jsonLines(stdout).find((o) => o.id === 2);
      if (!reply) break;
      rows.push(...readRows(getByPath(reply, cfg.list_path || 'result.data'), cfg));
      const next = cfg.cursor_path ? getByPath(reply, cfg.cursor_path) : undefined;
      if (typeof next !== 'string' || !next) break;
      if (seenCursors.has(next)) { notes.push('cursor-loop'); break; }
      seenCursors.add(next);
      cursor = next;
      if (page === MAX_PAGES - 1) notes.push(`page-cap:${MAX_PAGES}`);
    }
    return rows;
  }

  throw new Error(`unknown-probe-kind:${(cfg as { kind: string }).kind}`);
}

/**
 * A CLI may list POINTERS beside models: `default` and `opus` can both resolve to
 * claude-opus-5-5. The picker's own "use the default brain" row is the empty model, so
 * the CLI's default pointer rendered as a second default right beneath it.
 *
 * Dropping every alias is wrong too — `opus` is the ONLY id that reaches Opus 5.5. So:
 * drop the default pointer, then keep ONE id per resolved model, preferring the concrete
 * id when the CLI lists one.
 */
function collapseAliases(raw: RawModel[], cfg: ModelProbeConfig): RawModel[] {
  const rows = cfg.default_id ? raw.filter((r) => r.id !== cfg.default_id) : raw;
  if (!cfg.resolved_key) return rows;
  const byTarget = new Map<string, RawModel>();
  for (const row of rows) {
    const target = row.resolved || row.id;
    const held = byTarget.get(target);
    if (!held) { byTarget.set(target, row); continue; }
    // A row whose id IS the model it resolves to is the concrete one.
    if (held.id !== target && row.id === target) byTarget.set(target, row);
  }
  return rows.filter((row) => byTarget.get(row.resolved || row.id) === row);
}

function toModels(raw: RawModel[], staticModels: StaticModel[]): ModelInfo[] {
  const byId = new Map(staticModels.map((m) => [m.id, m]));
  const out: ModelInfo[] = [];
  const seen = new Set<string>();
  for (const row of raw) {
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    const known = byId.get(row.id);
    // Verified static capabilities are kept; the LIVE label wins, because the
    // login is the source of truth for what this operator sees.
    const capabilities: ModelCapabilities = known
      ? { ...known.capabilities }
      : { ...UNKNOWN_CAPABILITIES };
    const unverified = known ? [...(known.capabilities_unverified || [])] : [...UNKNOWN_FIELDS];
    if (row.modalities) {
      capabilities.text = row.modalities.includes('text');
      capabilities.image = row.modalities.includes('image');
      capabilities.audio = row.modalities.includes('audio');
      capabilities.video = row.modalities.includes('video');
      for (const f of ['image', 'audio', 'video']) {
        const i = unverified.indexOf(f);
        if (i >= 0) unverified.splice(i, 1);
      }
    }
    out.push({
      id: row.id,
      label: row.label || known?.label || row.id,
      capabilities,
      ...(unverified.length ? { capabilities_unverified: unverified } : {}),
    });
  }
  return out;
}

function staticFallback(staticModels: StaticModel[], reason: string): CatalogResult {
  return {
    valid_ids: staticModels.map((m) => m.id),
    models: staticModels.map((m) => ({
      id: m.id,
      label: m.label,
      capabilities: m.capabilities,
      ...(m.capabilities_unverified?.length ? { capabilities_unverified: m.capabilities_unverified } : {}),
    })),
    source: 'static-fallback',
    reason,
  };
}

/**
 * Probe one provider's live catalog. Never throws: a failed probe degrades to
 * the static list with a reason, so a broken CLI cannot empty the picker.
 */
export function probeModelCatalog(
  cfg: ModelProbeConfig,
  staticModels: StaticModel[] = [],
  exec: ProbeExec = defaultProbeExec,
): CatalogResult {
  const notes: string[] = [];
  let raw: RawModel[];
  try {
    raw = runProbe(cfg, exec, notes);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith('unknown-probe-kind:')) return staticFallback(staticModels, msg);
    return staticFallback(staticModels, `probe-failed:${msg.split('\n')[0].slice(0, 120)}`);
  }
  const models = toModels(collapseAliases(raw, cfg), staticModels);
  if (!models.length) return staticFallback(staticModels, 'probe-empty');
  const offered = new Set(models.map((m) => m.id));
  const valid_ids = [...models.map((m) => m.id), ...raw.map((r) => r.id).filter((id) => !offered.has(id))];
  return { models, valid_ids, source: 'probe', ...(notes.length ? { reason: notes.join(',') } : {}) };
}
