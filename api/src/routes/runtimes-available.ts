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
import { Router, type Request, type Response } from 'express';
import { execFileSync } from 'node:child_process';
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
  kind: 'cli-json' | 'file-json-key' | 'file-json-expiry';
  cmd?: string;
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

export interface ProbeDeps {
  loadProviders: () => ProviderConfig[];
  isInstalled: (provider: ProviderConfig) => boolean;
  probeAuth: (provider: ProviderConfig) => AuthResult;
  loadModelCatalog: (provider: ProviderConfig, auth?: AuthResult) => CatalogResult;
  publishLiveCatalog: (providers: ProviderResult[]) => void;
  now: () => number;
}

export function makeDefaultDeps(providersPath?: string): ProbeDeps {
  return {
    loadProviders: () => defaultLoadProviders(providersPath),
    isInstalled: defaultIsInstalled,
    probeAuth: defaultProbeAuth,
    loadModelCatalog: defaultLoadModelCatalog,
    publishLiveCatalog: (providers) => defaultPublishLiveCatalog(providers),
    now: () => Date.now(),
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
      models: catalog.models,
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

  router.get('/available', (_req: Request, res: Response) => {
    res.json(getCachedOrFresh());
  });

  // Self-heal: force a full re-probe, discarding whatever the cache held. This one STAYS
  // blocking on purpose — an explicit refresh is a request for fresh truth, not for speed.
  router.post('/available/refresh', (_req: Request, res: Response) => {
    invalidate();
    res.json(getFresh());
  });

  const peek = () => (cache && deps.now() < cache.expiresAt ? cache.value : null);

  const forceRefresh = () => {
    invalidate();
    return getFresh();
  };

  return { router, invalidate, getCached: getCachedOrFresh, forceRefresh, peek };
}

const production = createRuntimesAvailableRouter();

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
