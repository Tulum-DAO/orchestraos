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
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// api/src/routes -> repo root is three levels up.
const DEFAULT_PROVIDERS_PATH = resolvePath(__dirname, '../../../config/providers.json');

const TTL_S = 300;

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
  model_catalog: { source: 'registry' | 'cli' | 'static'; static?: StaticModel[] };
}

export interface AuthResult {
  authed: boolean | 'unverified';
  auth_reason?: string;
}

export interface ModelInfo {
  id: string;
  label: string;
  capabilities: ModelCapabilities;
}

export interface ProviderResult {
  id: string;
  label: string;
  logo_svg: string;
  installed: boolean;
  authed: boolean | 'unverified';
  auth_reason?: string;
  models: ModelInfo[];
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

function runAuthProbe(probe: AuthProbeConfig): AuthResult {
  try {
    if (probe.kind === 'cli-json') {
      if (!probe.cmd) return { authed: 'unverified', auth_reason: 'no-probe-cmd-configured' };
      const [bin, ...args] = probe.cmd.split(' ');
      let out: string;
      try {
        out = execFileSync(bin, args, { stdio: ['ignore', 'pipe', 'ignore'] }).toString();
      } catch (e) {
        // The CLI probe itself failed to run (not installed / non-zero exit
        // without JSON) — fall back rather than guessing.
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

export function defaultLoadModelCatalog(provider: ProviderConfig): ModelInfo[] {
  if (provider.model_catalog.source === 'static') {
    return (provider.model_catalog.static || []).map((m) => ({
      id: m.id,
      label: m.label,
      capabilities: m.capabilities,
    }));
  }
  // 'registry' / 'cli' sources: not yet wired (no live consumer needs them
  // for B2's static-fleet-model set) — LOUD unverified empty list rather than
  // guessing a shape.
  return [];
}

// ---- probe deps + cache ------------------------------------------------

export interface ProbeDeps {
  loadProviders: () => ProviderConfig[];
  isInstalled: (provider: ProviderConfig) => boolean;
  probeAuth: (provider: ProviderConfig) => AuthResult;
  loadModelCatalog: (provider: ProviderConfig) => ModelInfo[];
  now: () => number;
}

export function makeDefaultDeps(providersPath?: string): ProbeDeps {
  return {
    loadProviders: () => defaultLoadProviders(providersPath),
    isInstalled: defaultIsInstalled,
    probeAuth: defaultProbeAuth,
    loadModelCatalog: defaultLoadModelCatalog,
    now: () => Date.now(),
  };
}

export function probeAll(deps: ProbeDeps): RuntimesAvailableResponse {
  const providers = deps.loadProviders();
  const results: ProviderResult[] = providers.map((provider) => {
    const installed = deps.isInstalled(provider);
    // Not installed => authed is meaningless; report it LOUD as false rather
    // than probing (and never 'unverified' — absence of the binary is a
    // known, not unknown, state).
    const auth = installed
      ? deps.probeAuth(provider)
      : { authed: false as const, auth_reason: 'not-installed' };
    const models = deps.loadModelCatalog(provider);
    return {
      id: provider.id,
      label: provider.label,
      logo_svg: provider.logo_svg,
      installed,
      authed: auth.authed,
      auth_reason: auth.auth_reason,
      models,
    };
  });
  return { providers: results, probed_at: deps.now(), ttl_s: TTL_S };
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
export function createRuntimesAvailableRouter(deps: ProbeDeps = makeDefaultDeps()): {
  router: Router;
  invalidate: () => void;
} {
  const router = Router();
  let cache: CacheEntry | null = null;

  const invalidate = () => {
    cache = null;
  };

  const getFresh = (): RuntimesAvailableResponse => {
    const value = probeAll(deps);
    cache = { value, expiresAt: deps.now() + TTL_S * 1000 };
    return value;
  };

  const getCachedOrFresh = (): RuntimesAvailableResponse => {
    if (cache && deps.now() < cache.expiresAt) return cache.value;
    return getFresh();
  };

  router.get('/available', (_req: Request, res: Response) => {
    res.json(getCachedOrFresh());
  });

  // Self-heal: force a full re-probe, discarding whatever the cache held.
  router.post('/available/refresh', (_req: Request, res: Response) => {
    invalidate();
    res.json(getFresh());
  });

  return { router, invalidate };
}

const production = createRuntimesAvailableRouter();

// Exported so a future spawn/401 failure signal can self-heal the cache
// without waiting out the 300s TTL (contract requirement; no caller wired
// yet — wiring that trigger is a separate ticket, not part of B2's surface).
export const invalidateRuntimesCache = production.invalidate;

export default production.router;
