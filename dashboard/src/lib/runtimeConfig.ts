/**
 * Deployment values the dashboard reads at RUNTIME, not baked into the bundle.
 *
 * Source: `/runtime-config.json` next to index.html, fetched once before the first render
 * (main.tsx). The file is OPTIONAL and this repo does not ship one: a deployment drops its own
 * copy into `dashboard/public/` (vite copies it into dist) or straight into the served
 * directory. Absent, unreadable, slow or malformed -> the public defaults below, never a
 * blank page.
 *
 *   { "operatorUserId": "alice", "features": { "arturo": false }, "hiddenViews": ["/analytics"] }
 *
 * Fetched with `cache: 'no-store'` because static servers commonly mark every non-HTML file
 * `immutable` for a year (this repo's dashboard-proxy.js does), which would freeze a changed
 * value in every browser that ever loaded the old one.
 *
 * DETECTION, only where the file is silent: with no explicit `features.arturo`, a 404 from
 * GET /api/arturo/health (the route or its backend is not installed) switches Arturo off. A
 * 502-504 is a service still STARTING and keeps it on; so do 401, a network error and a slow
 * answer. An explicit flag always wins and then no probe is sent at all. newAgent and
 * providerSignIn have no side-effect-free probe (their routes are POST, and a GET on a POST-only
 * Express route is a 404 whether or not it exists), so they follow the file only.
 */

/** Surfaces a deployment can switch OFF when its API does not serve them. A switched-off surface
 *  does not render at all: no button that leads to a 404. Every one defaults to ON. */
export interface Features {
  /** The Arturo assistant: the home page at "/", the top-bar Arturo button and its pane, its sidebar entry
   *  (/api/arturo/*). Off: "/" opens the Overview instead. */
  arturo: boolean;
  /** The New Agent button and modal on Overview and Agents (/api/agents/new, login-shell). */
  newAgent: boolean;
  /** Connecting a provider from the model picker: the connect modal behind a disconnected
   *  provider tile and the "Add a provider" tile (/api/agents/login-shell, :id/sign-in-url). */
  providerSignIn: boolean;
}

export type FeatureName = keyof Features;

export interface RuntimeConfig {
  /** The human operator's user id: the key their insights, profile, telemetry and messages
   *  are stored under. Public placeholder 'operator'. */
  operatorUserId: string;
  features: Features;
  /** Dashboard views this deployment does not offer, as route paths ("/analytics"). A hidden view
   *  renders Page not found and drops out of the sidebar and the command palette. */
  hiddenViews: string[];
}

export const DEFAULT_RUNTIME_CONFIG: RuntimeConfig = {
  operatorUserId: 'operator',
  features: { arturo: true, newAgent: true, providerSignIn: true },
  hiddenViews: [],
};

// One path segment or more, lowercase route words only. "/" itself is not hideable: the index is
// governed by features.arturo.
const VIEW_PATH = /^\/[a-z0-9-]+(\/[a-z0-9-]+)*$/;

/** Pure: the valid entries of an untrusted hiddenViews value, normalised (trimmed, lowercased, no
 *  trailing slash). Anything malformed is dropped with a console warning, never half-applied. */
export function resolveHiddenViews(raw: unknown, warn: (m: string) => void = (m) => console.warn(m)): string[] {
  if (raw === undefined) return [];
  if (!Array.isArray(raw)) {
    warn(`runtime-config: hiddenViews must be an array of paths like "/analytics"; ignored (${JSON.stringify(raw)})`);
    return [];
  }
  const out: string[] = [];
  for (const v of raw) {
    const p = typeof v === 'string' ? v.trim().toLowerCase().replace(/\/+$/, '') : null;
    if (p && VIEW_PATH.test(p)) { if (!out.includes(p)) out.push(p); }
    else warn(`runtime-config: hiddenViews entry ${JSON.stringify(v)} is not a view path like "/analytics"; ignored`);
  }
  return out;
}

// The id goes into URL paths (/adaptive/<id>/insights) and message fields, so only a plain
// identifier is accepted; anything else falls back rather than being half-used.
const USER_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/** Pure: merge an untrusted parsed value over the defaults, field by field. */
export function resolveRuntimeConfig(raw: unknown): RuntimeConfig {
  const cfg = { ...DEFAULT_RUNTIME_CONFIG, features: { ...DEFAULT_RUNTIME_CONFIG.features }, hiddenViews: [] as string[] };
  if (raw && typeof raw === 'object') {
    const id = (raw as Record<string, unknown>).operatorUserId;
    if (typeof id === 'string' && USER_ID.test(id.trim())) cfg.operatorUserId = id.trim();
    // Only a real boolean switches a feature: "false" (a string) or 0 is a typo, not an
    // instruction, and a typo must not hide a working surface.
    const f = (raw as Record<string, unknown>).features;
    if (f && typeof f === 'object') {
      for (const name of Object.keys(cfg.features) as FeatureName[]) {
        const v = (f as Record<string, unknown>)[name];
        if (typeof v === 'boolean') cfg.features[name] = v;
      }
    }
    cfg.hiddenViews = resolveHiddenViews((raw as Record<string, unknown>).hiddenViews);
  }
  return cfg;
}

let current: RuntimeConfig = DEFAULT_RUNTIME_CONFIG;

export function runtimeConfig(): RuntimeConfig {
  return current;
}

export function operatorUserId(): string {
  return current.operatorUserId;
}

export function featureEnabled(name: FeatureName): boolean {
  return current.features[name];
}

export function hiddenViews(): string[] {
  return current.hiddenViews;
}

/** The value the file sets for a feature, or undefined when it says nothing (only booleans count). */
export function explicitFeature(raw: unknown, name: FeatureName): boolean | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const f = (raw as Record<string, unknown>).features;
  if (!f || typeof f !== 'object') return undefined;
  const v = (f as Record<string, unknown>)[name];
  return typeof v === 'boolean' ? v : undefined;
}

/** Pure: what the health probe's status says about Arturo. Only "not there" (404) turns it off;
 *  null = no answer (network, timeout), which is no evidence of absence. */
export function arturoFromHealthStatus(status: number | null): boolean {
  return status !== 404;
}

/** Test seam, and what loadRuntimeConfig() commits. */
export function setRuntimeConfig(raw: unknown): RuntimeConfig {
  current = resolveRuntimeConfig(raw);
  return current;
}

/** Fetch and commit the deployment's values. Never rejects: any failure keeps the defaults. */
export async function loadRuntimeConfig(
  fetchImpl: typeof fetch = fetch,
  url = '/runtime-config.json',
  timeoutMs = 3_000,
  probeTimeoutMs = 1_500,
): Promise<RuntimeConfig> {
  let raw: unknown = null;
  try {
    const res = await fetchImpl(url, { cache: 'no-store', signal: AbortSignal.timeout(timeoutMs) });
    // A missing file usually comes back as the SPA fallback (index.html, 200), not a 404, so a
    // non-JSON body is "no config", not an error.
    if (res.ok) raw = await res.json();
  } catch { /* no file, or not JSON: defaults */ }
  const cfg = setRuntimeConfig(raw);
  if (explicitFeature(raw, 'arturo') === undefined) {
    let status: number | null = null;
    try {
      const res = await fetchImpl('/api/arturo/health', { signal: AbortSignal.timeout(probeTimeoutMs) });
      status = res.status;
    } catch { /* no answer: no evidence it is absent */ }
    if (!arturoFromHealthStatus(status)) {
      current = { ...cfg, features: { ...cfg.features, arturo: false } };
    }
  }
  return current;
}
