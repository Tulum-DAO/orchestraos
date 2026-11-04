/**
 * runtimes-available.ts — Agent Page v1 B2, the SELF-HEALING provider/model
 * catalog (DEC-1789508247033721 §2 B2).
 *
 * GET  /api/runtimes/available          cached (TTL 300s) provider+model probe
 * POST /api/runtimes/available/refresh  force self-heal re-probe, bust cache
 *
 * Providers come from config/providers.json (UI + probe METADATA only).
 * Runtime truth stays scripts/runtime_signatures.py VALID_RUNTIMES — this
 * route never hardcodes a provider id in its core loop; every provider is
 * handled generically by iterating the registry and dispatching on
 * `auth_probe.kind` (a probe STRATEGY, not an identity), so adding a 4th
 * provider needs zero route-code changes.
 *
 * Self-heal: an in-process cache is the fast path; POST /refresh (or any
 * caller invoking `invalidateRuntimesCache()` — e.g. a future spawn/401
 * failure hook) forces the next GET to re-probe from scratch rather than
 * serving a stale "installed/authed" verdict.
 *
 * W1 (unauthed/unavailable providers fail LOUD): every probe that cannot
 * positively confirm a state reports `authed: 'unverified'` with a populated
 * `auth_reason` — never silently coerced to true or false.
 */
import { Router, type NextFunction, type Request, type Response } from 'express';
import { execFile, execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';
import { probeModelCatalog, type CatalogResult, type ModelProbeConfig } from './model-catalog.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
// api/src/routes -> repo root is three levels up.
const DEFAULT_PROVIDERS_PATH = resolvePath(__dirname, '../../../config/providers.json');

const TTL_S = 300;
/**
 * How old a DISK-seeded catalog may be and still be served at boot.
 *
 * The 300s TTL decides when to REFRESH; this ceiling decides whether a persisted
 * catalog is trustworthy at all. They are different questions: a 10-minute-old file is
 * stale but certainly still true, while a week-old one may offer a model the provider has
 * retired — and the proxy validates a saved pick against this catalog, so serving a
 * long-dead entry turns a fast picker into a rejected model.
 */
export const DISK_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const DEFAULT_RESPONSE_CACHE_PATH = resolvePath(__dirname, '../../../state/runtimes-available-cache.json');
// The proxy validates an explicitly picked model against this file (union'd with
// providers.json static ids). Offering a model in the picker and then refusing it is
// the bug this closes, so a PROBED catalog is published where validation can see it.
const DEFAULT_LIVE_CATALOG_PATH = resolvePath(__dirname, '../../../state/model-catalog-live.json');
/** The seat-only models (contract/seat-models.json), the same list scripts/agent_model.py offers a seat. */
export const SEAT_MODELS_PATH = resolvePath(__dirname, '../../../contract/seat-models.json');

export interface ModelCapabilities {
  text: boolean;
  image: boolean;
  audio: boolean;
  video: boolean;
  context_window: number | null;
}

export interface StaticModel {
  id: string;
  label: string;
  capabilities: ModelCapabilities;
  capabilities_unverified?: string[];
}

export interface AuthProbeConfig {
  kind: 'cli-json' | 'file-json-key' | 'file-json-expiry' | 'cli-text';
  cmd?: string;
  /** cli-text: a stdout line matching this regex means signed in ... */
  success_pattern?: string;
  /** ... and one matching this means signed out (checked first). Neither -> unverified. */
  failure_pattern?: string;
  success_key?: string;
  path?: string;
  key?: string;
  expiry_key?: string;
  fallback?: AuthProbeConfig;
}

export interface ProviderConfig {
  id: string;
  label: string;
  logo_svg: string;
  cli: string;
  aliases: string[];
  detect: { cmd: string };
  auth_probe: AuthProbeConfig;
  model_catalog: {
    source: 'registry' | 'cli' | 'static' | 'probe';
    /** Live, login-specific list (source: 'probe'); `static` stays the fallback. */
    probe?: ModelProbeConfig;
    static?: StaticModel[];
  };
}

export interface AuthResult {
  authed: boolean | 'unverified';
  auth_reason?: string;
}

export interface ModelInfo {
  id: string;
  label: string;
  capabilities: ModelCapabilities;
  /** Fields we have NOT confirmed for this model — never silently assumed. */
  capabilities_unverified?: string[];
  /** The CLI's own description of the model, when its catalog probe reads one (probe.description_key). */
  description?: string;
  /** Set only on a model an agent seat may use and Arturo may not (contract/seat-models.json), with the reason a
   *  picker shows when it dims it. Absent on every other row. */
  seat_only?: true;
  seat_only_reason?: string;
}

export interface ProviderResult {
  id: string;
  label: string;
  logo_svg: string;
  installed: boolean;
  authed: boolean | 'unverified';
  auth_reason?: string;
  models: ModelInfo[];
  /** Where `models` came from: a live probe, the static list, or the static
   *  list AFTER a probe failed (with `model_catalog_reason` saying why). */
  model_catalog_source: CatalogResult['source'];
  model_catalog_reason?: string;
  /** Ids validation accepts for this provider — a superset of `models` (see CatalogResult). */
  model_catalog_valid_ids: string[];
  /** The model an empty model runs for this login, when the CLI says (see CatalogResult). */
  model_catalog_default?: string;
  /** The installed CLI's version ("2.1.295"), from `<cli> --version` (VersionCache below); absent when the CLI is not
   *  installed or printed no version. Added when the response is served, never stored in the response cache. */
  version?: string;
}

export interface RuntimesAvailableResponse {
  providers: ProviderResult[];
  probed_at: number;
  ttl_s: number;
}

// ---- default (real) dependency implementations -----------------------

function expandHome(p: string): string {
  return p.startsWith('~') ? join(homedir(), p.slice(1)) : p;
}

function getByPath(obj: unknown, dotted: string): unknown {
  return dotted.split('.').reduce<unknown>((acc, key) => {
    if (acc && typeof acc === 'object' && key in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj);
}

export function defaultLoadProviders(path: string = DEFAULT_PROVIDERS_PATH): ProviderConfig[] {
  const raw = readFileSync(path, 'utf-8');
  const parsed = JSON.parse(raw) as { providers: ProviderConfig[] };
  return parsed.providers;
}

/** The version in a CLI's `--version` answer: only from a run that exited 0, and only from the FIRST line, either at
 *  its start ("2.1.295 (Claude Code)") or right after one word naming the CLI ("codex-cli 0.98.1", "grok version 4.7").
 *  Anything else is no version: an error such as "requires Node.js 18.0.0" must never show as one. */
export function parseCliVersion(stdout: string, exitCode: number | null = 0): string | null {
  if (exitCode !== 0) return null;
  const first = (stdout || '').split(/\r?\n/).find((l) => l.trim()) ?? '';
  const m = first.match(/^\s*(?:[A-Za-z][\w.-]*\s+)?(?:version\s+)?v?(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.]+)?)(?=$|[\s(,])/i);
  return m ? m[1] : null;
}

/** Runs `<cli> --version` without blocking: its exit code and stdout. A failure to start or a timeout is a non-zero
 *  code. Each run is bounded at 3 s. */
export type VersionRunner = (cli: string) => Promise<{ code: number | null; stdout: string }>;

export const defaultVersionRunner: VersionRunner = (cli) => new Promise((resolve) => {
  execFile(cli, ['--version'], { timeout: 3000, encoding: 'utf-8', windowsHide: true }, (err, stdout) => {
    const c = err ? (err as { code?: unknown }).code : 0;
    const code = typeof c === 'number' ? c : 1;   // a spawn error ('ENOENT') or a kill on timeout is a failure
    resolve({ code, stdout: String(stdout ?? '') });
  });
});

/** How long a CLI's version is kept: versions change on an upgrade, not between two probes. */
export const VERSION_TTL_MS = 60 * 60 * 1000;

export interface VersionCache {
  /** {provider id: version} for these installed CLIs: the cached answer while it is fresh and covers them, else every
   *  `--version` run at once (Promise.all; one batch in flight at a time). Never throws. */
  get: (clis: { id: string; cli: string }[]) => Promise<Record<string, string>>;
}

export function createVersionCache(run: VersionRunner = defaultVersionRunner, now: () => number = Date.now,
  ttlMs: number = VERSION_TTL_MS): VersionCache {
  let entry: { at: number; key: string; versions: Record<string, string> } | null = null;
  let inflight: { key: string; p: Promise<Record<string, string>> } | null = null;
  return {
    get: async (clis) => {
      const key = clis.map((c) => `${c.id}=${c.cli}`).sort().join(',');
      if (entry && entry.key === key && now() - entry.at < ttlMs) return entry.versions;
      if (inflight && inflight.key === key) return inflight.p;
      const p = Promise.all(clis.map(async (c) => {
        try {
          const r = await run(c.cli);
          return [c.id, parseCliVersion(r.stdout, r.code)] as const;
        } catch {
          return [c.id, null] as const;
        }
      })).then((pairs) => {
        const versions: Record<string, string> = {};
        for (const [id, v] of pairs) if (v) versions[id] = v;
        entry = { at: now(), key, versions };
        return versions;
      }).finally(() => { inflight = null; });
      inflight = { key, p };
      return p;
    },
  };
}

/** The response with each installed CLI's version added (configs give the CLI names the response leaves out). */
export async function withVersions(resp: RuntimesAvailableResponse, configs: ProviderConfig[], cache: VersionCache | null): Promise<RuntimesAvailableResponse> {
  if (!cache) return resp;
  const installed = resp.providers.filter((p) => p.installed);
  const clis = installed.map((p) => ({ id: p.id, cli: configs.find((c) => c.id === p.id)?.cli ?? '' })).filter((c) => c.cli);
  if (!clis.length) return resp;
  const versions = await cache.get(clis);
  return { ...resp, providers: resp.providers.map((p) => (p.installed && versions[p.id] ? { ...p, version: versions[p.id] } : p)) };
}

export function defaultIsInstalled(provider: ProviderConfig): boolean {
  try {
    execFileSync('which', [provider.cli], { stdio: ['ignore', 'pipe', 'ignore'] });
    return true;
  } catch {
    return false;
  }
}

/** A CLI that is LOGGED OUT prints its JSON and exits non-zero (`claude auth status` ->
 *  {"loggedIn": false}, status 1). That stdout is an ANSWER; reading it as "the probe failed"
 *  and falling back to a stale ~/.claude.json oauthAccount reported authed:true for a
 *  logged-out CLI (found by effect, 2026-09-18). Returns null when there is nothing usable. */
export function salvageCliJsonAnswer(stdout: string, successKey = 'loggedIn'): AuthResult | null {
  if (!stdout || !stdout.trim()) return null;
  try {
    const parsed = JSON.parse(stdout) as Record<string, unknown>;
    const value = parsed[successKey];
    if (typeof value === 'boolean') {
      return value ? { authed: true } : { authed: false, auth_reason: `${successKey}=false` };
    }
  } catch { /* not JSON */ }
  return null;
}

/** The cli-text verdict over a CLI's stdout (pure; the Python twin is runtime_probe.py).
 *  `grok models` answers "You are logged in with ..." or "You are not authenticated." and
 *  exits 0 either way, so the TEXT is the answer. */
export function cliTextVerdict(stdout: string, probe: Pick<AuthProbeConfig, 'success_pattern' | 'failure_pattern'>): AuthResult {
  if (probe.failure_pattern && new RegExp(probe.failure_pattern, 'm').test(stdout)) {
    return { authed: false, auth_reason: 'signed-out' };
  }
  if (probe.success_pattern && new RegExp(probe.success_pattern, 'm').test(stdout)) {
    return { authed: true };
  }
  return { authed: 'unverified', auth_reason: 'auth-probe-no-match' };
}

function runAuthProbe(probe: AuthProbeConfig): AuthResult {
  try {
    if (probe.kind === 'cli-json') {
      if (!probe.cmd) return { authed: 'unverified', auth_reason: 'no-probe-cmd-configured' };
      const [bin, ...args] = probe.cmd.split(' ');
      let out: string;
      try {
        out = execFileSync(bin, args, { stdio: ['ignore', 'pipe', 'ignore'] }).toString();
      } catch (e) {
        const salvaged = salvageCliJsonAnswer(
          (e as { stdout?: Buffer | string }).stdout?.toString?.() || '', probe.success_key || 'loggedIn');
        if (salvaged) return salvaged;
        // The CLI probe itself failed to run (not installed / no usable output) —
        // fall back rather than guessing.
        if (probe.fallback) return runAuthProbe(probe.fallback);
        return { authed: 'unverified', auth_reason: 'auth-probe-cmd-failed' };
      }
      const parsed = JSON.parse(out);
      const key = probe.success_key || 'loggedIn';
      if (typeof parsed[key] === 'boolean') {
        return parsed[key]
          ? { authed: true }
          : { authed: false, auth_reason: `${key}=false` };
      }
      if (probe.fallback) return runAuthProbe(probe.fallback);
      return { authed: 'unverified', auth_reason: `auth-probe-missing-key:${key}` };
    }

    if (probe.kind === 'cli-text') {
      if (!probe.cmd) return { authed: 'unverified', auth_reason: 'no-probe-cmd-configured' };
      const [bin, ...args] = probe.cmd.split(' ');
      let out: string;
      try {
        out = execFileSync(bin, args, { stdio: ['ignore', 'pipe', 'ignore'], timeout: 20000 }).toString();
      } catch (e) {
        out = (e as { stdout?: Buffer | string }).stdout?.toString?.() || '';
        if (!out.trim()) return { authed: 'unverified', auth_reason: 'auth-probe-cmd-failed' };
      }
      return cliTextVerdict(out, probe);
    }

    if (probe.kind === 'file-json-key') {
      const path = expandHome(probe.path || '');
      if (!existsSync(path)) return { authed: false, auth_reason: 'auth-file-missing' };
      const parsed = JSON.parse(readFileSync(path, 'utf-8'));
      const present = probe.key ? getByPath(parsed, probe.key) : undefined;
      if (present === undefined || present === null) {
        return { authed: false, auth_reason: `auth-file-missing-key:${probe.key}` };
      }
      return { authed: true };
    }

    if (probe.kind === 'file-json-expiry') {
      const path = expandHome(probe.path || '');
      if (!existsSync(path)) return { authed: false, auth_reason: 'auth-file-missing' };
      const parsed = JSON.parse(readFileSync(path, 'utf-8'));
      const expiryRaw = probe.expiry_key ? getByPath(parsed, probe.expiry_key) : undefined;
      if (typeof expiryRaw !== 'string') {
        return { authed: 'unverified', auth_reason: `auth-file-missing-expiry:${probe.expiry_key}` };
      }
      const expiryMs = Date.parse(expiryRaw);
      if (Number.isNaN(expiryMs)) {
        return { authed: 'unverified', auth_reason: 'auth-file-unparseable-expiry' };
      }
      return expiryMs > Date.now()
        ? { authed: true }
        : { authed: false, auth_reason: 'token-expired' };
    }

    return { authed: 'unverified', auth_reason: `unknown-probe-kind:${(probe as { kind: string }).kind}` };
  } catch {
    return { authed: 'unverified', auth_reason: 'auth-probe-threw' };
  }
}

export function defaultProbeAuth(provider: ProviderConfig): AuthResult {
  return runAuthProbe(provider.auth_probe);
}

function staticCatalog(provider: ProviderConfig, reason?: string): CatalogResult {
  const staticIds = (provider.model_catalog.static || []).map((m) => m.id);
  const models = (provider.model_catalog.static || []).map((m) => ({
    id: m.id,
    label: m.label,
    capabilities: m.capabilities,
    ...(m.capabilities_unverified?.length ? { capabilities_unverified: m.capabilities_unverified } : {}),
  }));
  return reason
    ? { models, valid_ids: staticIds, source: 'static-fallback', reason }
    : { models, valid_ids: staticIds, source: 'static' };
}

/**
 * The catalog for one provider. A `probe` source asks the operator's OWN CLI
 * what it can run (see model-catalog.ts); anything else serves the static
 * list. A probe is only worth running against a CLI that is installed and
 * authed — a logged-out CLI cannot answer, and waiting for it to fail would
 * stall the route.
 */
export function defaultLoadModelCatalog(provider: ProviderConfig, auth?: AuthResult): CatalogResult {
  const probe = provider.model_catalog.probe;
  if (provider.model_catalog.source !== 'probe' || !probe) {
    if (provider.model_catalog.source === 'static') return staticCatalog(provider);
    // 'registry' / 'cli' sources: no live consumer and no agreed shape — the
    // static list, said out loud, rather than a guess.
    return staticCatalog(provider, `unwired-source:${provider.model_catalog.source}`);
  }
  if (auth && auth.authed !== true) {
    return staticCatalog(provider, `not-authed:${auth.auth_reason || auth.authed}`);
  }
  return probeModelCatalog(probe, provider.model_catalog.static || []);
}

// ---- probe deps + cache ------------------------------------------------

/** Atomic, best-effort: this is a cache. A failure to publish must never fail a probe. */
export function defaultPublishLiveCatalog(
  providers: ProviderResult[],
  path: string = DEFAULT_LIVE_CATALOG_PATH,
): void {
  try {
    const live: Record<string, string[]> = {};
    const defaults: Record<string, string> = {};
    for (const p of providers) {
      // Only a LIVE answer is published; a static fallback is already known to the reader.
      if (p.model_catalog_source !== 'probe') continue;
      // The VALIDATION set, not the offered set: a collapsed alias is still a real
      // --model argument, and a saved pick of one must keep working.
      live[p.id] = p.model_catalog_valid_ids;
      if (p.model_catalog_default) defaults[p.id] = p.model_catalog_default;
    }
    if (!Object.keys(live).length) return;
    mkdirSync(dirname(path), { recursive: true });
    const tmp = `${path}.tmp`;
    writeFileSync(tmp, `${JSON.stringify({ providers: live, defaults, probed_at: Date.now() }, null, 1)}\n`);
    renameSync(tmp, path);
  } catch {
    /* cache only — the picker and the probe are unaffected */
  }
}

export interface SeatModels { reason: string; models: Record<string, { id: string; label: string }[]> }

/** contract/seat-models.json, or null when it is missing or malformed: then no seat-only rows are listed. */
export function readSeatModels(path: string = SEAT_MODELS_PATH): SeatModels | null {
  try {
    const doc = JSON.parse(readFileSync(path, 'utf-8'));
    if (typeof doc?.seat_only_reason !== 'string' || !doc.models || typeof doc.models !== 'object') return null;
    const models: SeatModels['models'] = {};
    for (const [rt, rows] of Object.entries(doc.models as Record<string, unknown>)) {
      if (!Array.isArray(rows)) continue;
      models[rt] = rows.filter((r): r is { id: string; label: string } =>
        !!r && typeof r.id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,99}$/.test(r.id) && typeof r.label === 'string');
    }
    return { reason: doc.seat_only_reason, models };
  } catch { return null; }
}

/** The catalog's rows with the seat-only ones added, each right after the model it is the 1M version of (its id
 *  without the "[1m]" suffix), with that model's capabilities. Only `models` changes: `valid_ids` is what the proxy
 *  and Arturo accept, and a seat-only id must stay out of it. */
export function withSeatModels(models: ModelInfo[], runtime: string, seat: SeatModels | null): ModelInfo[] {
  const extra = seat?.models[runtime] || [];
  if (!extra.length || !models.length) return models;   // no catalog: nothing to be the 1M version of
  const out = [...models];
  for (const m of extra) {
    if (out.some((r) => r.id === m.id)) continue;
    const baseId = m.id.replace(/\[1m\]$/i, '');
    const at = out.findIndex((r) => r.id === baseId);
    const base = at >= 0 ? out[at] : undefined;
    const row: ModelInfo = {
      id: m.id,
      label: m.label,
      capabilities: base ? { ...base.capabilities, context_window: 1_000_000 }
        : { text: true, image: false, audio: false, video: false, context_window: 1_000_000 },
      ...(base?.capabilities_unverified ? { capabilities_unverified: base.capabilities_unverified }
        : base ? {} : { capabilities_unverified: ['image', 'audio', 'video'] }),
      seat_only: true,
      seat_only_reason: seat!.reason,
    };
    if (at >= 0) {
      let k = at + 1;
      while (k < out.length && out[k].seat_only && out[k].id.replace(/\[1m\]$/i, '') === baseId) k++;
      out.splice(k, 0, row);
    } else out.push(row);
  }
  return out;
}

export interface ProbeDeps {
  loadProviders: () => ProviderConfig[];
  isInstalled: (provider: ProviderConfig) => boolean;
  probeAuth: (provider: ProviderConfig) => AuthResult;
  loadModelCatalog: (provider: ProviderConfig, auth?: AuthResult) => CatalogResult;
  publishLiveCatalog: (providers: ProviderResult[]) => void;
  now: () => number;
  /** The seat-only models to list, marked (contract/seat-models.json). Absent: none. */
  seatModels?: () => SeatModels | null;
}

export function makeDefaultDeps(providersPath?: string): ProbeDeps {
  return {
    loadProviders: () => defaultLoadProviders(providersPath),
    isInstalled: defaultIsInstalled,
    probeAuth: defaultProbeAuth,
    loadModelCatalog: defaultLoadModelCatalog,
    publishLiveCatalog: (providers) => defaultPublishLiveCatalog(providers),
    now: () => Date.now(),
    seatModels: () => readSeatModels(),
  };
}

/**
 * `withModels: false` answers "what is installed and signed in" WITHOUT asking any CLI for
 * its model list. Auth probes are milliseconds; a live catalog probe is seconds. Callers
 * that never show a model (the login-shell path) must not pay for one, or the first click
 * after a restart is a multi-second request — a 502 behind a proxy.
 */
export function probeAll(deps: ProbeDeps, opts: { withModels?: boolean } = {}): RuntimesAvailableResponse {
  const withModels = opts.withModels !== false;
  const providers = deps.loadProviders();
  const seat = withModels && deps.seatModels ? deps.seatModels() : null;
  const results: ProviderResult[] = providers.map((provider) => {
    const installed = deps.isInstalled(provider);
    // Not installed => authed is meaningless; report it LOUD as false rather
    // than probing (and never 'unverified' — absence of the binary is a
    // known, not unknown, state).
    const auth = installed
      ? deps.probeAuth(provider)
      : { authed: false as const, auth_reason: 'not-installed' };
    const catalog: CatalogResult = withModels
      ? deps.loadModelCatalog(provider, auth)
      : { models: [], valid_ids: [], source: 'not-probed' };
    return {
      id: provider.id,
      label: provider.label,
      logo_svg: provider.logo_svg,
      installed,
      authed: auth.authed,
      auth_reason: auth.auth_reason,
      models: withSeatModels(catalog.models, provider.id, seat),
      model_catalog_source: catalog.source,
      model_catalog_valid_ids: catalog.valid_ids,
      ...(catalog.default_model ? { model_catalog_default: catalog.default_model } : {}),
      ...(catalog.reason ? { model_catalog_reason: catalog.reason } : {}),
    };
  });
  // Never publish from a run that did not ask: an empty catalog is not an answer.
  if (withModels) deps.publishLiveCatalog(results);
  return { providers: results, probed_at: deps.now(), ttl_s: TTL_S };
}

/** Persisted response cache. Separate from model-catalog-live.json, which holds only the
 *  VALIDATION ids the proxy needs — not the labels, capabilities or auth state the picker
 *  renders, so it cannot seed this response. */
export interface CacheIO {
  readCache: () => RuntimesAvailableResponse | null;
  writeCache: (value: RuntimesAvailableResponse) => void;
}

export function defaultCacheIO(path: string = DEFAULT_RESPONSE_CACHE_PATH): CacheIO {
  return {
    readCache: () => {
      if (!existsSync(path)) return null;
      return JSON.parse(readFileSync(path, 'utf8')) as RuntimesAvailableResponse;
    },
    writeCache: (value) => {
      mkdirSync(dirname(path), { recursive: true });
      const tmp = `${path}.tmp`;
      writeFileSync(tmp, `${JSON.stringify(value, null, 1)}\n`);
      renameSync(tmp, path);
    },
  };
}

interface CacheEntry {
  value: RuntimesAvailableResponse;
  expiresAt: number;
}

/**
 * Creates one router instance with its OWN cache + deps closure. Production
 * mounts a single instance (module-level `router` below); tests construct
 * their own with fake deps so probes never touch the real filesystem/CLIs.
 */
export function createRuntimesAvailableRouter(
  deps: ProbeDeps = makeDefaultDeps(),
  io: CacheIO = defaultCacheIO(),
  /** Adds each CLI's version to what is served. Null (tests' default): no versions, no CLI runs. */
  versions: VersionCache | null = null,
): {
  router: Router;
  invalidate: () => void;
  getCached: () => RuntimesAvailableResponse;
  /** Discard the cache and probe synchronously — what POST /refresh does. */
  forceRefresh: () => RuntimesAvailableResponse;
  /** The cached value if it is still fresh, else null — never probes. */
  peek: () => RuntimesAvailableResponse | null;
} {
  const router = Router();
  let cache: CacheEntry | null = null;
  let refreshing = false;

  const invalidate = () => {
    cache = null;
  };

  const getFresh = (): RuntimesAvailableResponse => {
    const value = probeAll(deps);
    cache = { value, expiresAt: deps.now() + TTL_S * 1000 };
    // Best-effort: this is a cache. A write failure must never fail the request, and must
    // never fail the probe that produced a perfectly good answer.
    try {
      io.writeCache(value);
    } catch {
      /* cache only */
    }
    return value;
  };

  // Seed from the last persisted catalog so a RESTART does not make the first caller wait
  // on a multi-second probe. A read failure is not fatal: a corrupt file must degrade to a
  // normal cold probe, never to a broken endpoint.
  try {
    const seed = io.readCache();
    if (seed && typeof seed.probed_at === 'number'
        && deps.now() - seed.probed_at < DISK_CACHE_MAX_AGE_MS) {
      cache = { value: seed, expiresAt: seed.probed_at + TTL_S * 1000 };
    }
  } catch {
    /* no seed; the first request probes as it always did */
  }

  /** Revalidate behind the response. Single-flight: five callers arriving on an expired
   *  cache must not start five probes. A throwing probe leaves the stale value in place. */
  const revalidate = () => {
    if (refreshing) return;
    refreshing = true;
    setImmediate(() => {
      try {
        getFresh();
      } catch {
        /* keep serving the last good catalog */
      } finally {
        refreshing = false;
      }
    });
  };

  const getCachedOrFresh = (): RuntimesAvailableResponse => {
    if (cache) {
      // STALE-WHILE-REVALIDATE: an expired entry still answers instantly. Blocking here was
      // the whole defect — every 300s, whoever opened the picker first paid the full probe.
      if (deps.now() >= cache.expiresAt) revalidate();
      return cache.value;
    }
    return getFresh();
  };

  const configs = (): ProviderConfig[] => { try { return deps.loadProviders(); } catch { return []; } };
  const serve = async (res: Response, value: RuntimesAvailableResponse) => {
    res.json(await withVersions(value, versions ? configs() : [], versions));
  };

  router.get('/available', (_req: Request, res: Response, next: NextFunction) => {
    try { serve(res, getCachedOrFresh()).catch(next); } catch (e) { next(e); }
  });

  // Self-heal: force a full re-probe, discarding whatever the cache held. This one STAYS
  // blocking on purpose — an explicit refresh is a request for fresh truth, not for speed.
  router.post('/available/refresh', (_req: Request, res: Response, next: NextFunction) => {
    invalidate();
    try { serve(res, getFresh()).catch(next); } catch (e) { next(e); }
  });

  const peek = () => (cache && deps.now() < cache.expiresAt ? cache.value : null);

  const forceRefresh = () => {
    invalidate();
    return getFresh();
  };

  return { router, invalidate, getCached: getCachedOrFresh, forceRefresh, peek };
}

const production = createRuntimesAvailableRouter(makeDefaultDeps(), defaultCacheIO(), createVersionCache());

/**
 * The same probe the route serves, through the SAME cache.
 *
 * Any other route that needs "what is installed and signed in" must come through here.
 * agents-new called probeAll(makeDefaultDeps()) directly, which was cheap while the model
 * catalog was a static list — once the catalog became a LIVE CLI probe it meant ~3.5s of
 * CLI spawning on every login-shell POST, and behind a proxy that is a 502 (operator:
 * "it requires I click it three times", 2026-09-29).
 */
export const getCachedRuntimes = production.getCached;

/**
 * For callers that need installed/authed only. Serves the shared cache when it is warm —
 * so the answer matches what the sheet is showing — and otherwise probes auth ALONE rather
 * than waiting on a live model probe it has no use for.
 */
export function getRuntimesForAuth(): ProviderResult[] {
  const cached = production.peek();
  if (cached) return cached.providers;
  return probeAll(makeDefaultDeps(), { withModels: false }).providers;
}

// Exported so a future spawn/401 failure signal can self-heal the cache
// without waiting out the 300s TTL (contract requirement; no caller wired
// yet — wiring that trigger is a separate ticket, not part of B2's surface).
export const invalidateRuntimesCache = production.invalidate;

export default production.router;
