/**
 * Deployment values the dashboard reads at RUNTIME, not baked into the bundle.
 *
 * Source: `/runtime-config.json` next to index.html, fetched once before the first render
 * (main.tsx). The file is OPTIONAL and this repo does not ship one: a deployment drops its own
 * copy into `dashboard/public/` (vite copies it into dist) or straight into the served
 * directory. Absent, unreadable, slow or malformed -> the public defaults below, never a
 * blank page.
 *
 *   { "operatorUserId": "alice" }
 *
 * Fetched with `cache: 'no-store'` because static servers commonly mark every non-HTML file
 * `immutable` for a year (this repo's dashboard-proxy.js does), which would freeze a changed
 * value in every browser that ever loaded the old one.
 */

export interface RuntimeConfig {
  /** The human operator's user id: the key their insights, profile, telemetry and messages
   *  are stored under. Public placeholder 'operator'. */
  operatorUserId: string;
}

export const DEFAULT_RUNTIME_CONFIG: RuntimeConfig = { operatorUserId: 'operator' };

// The id goes into URL paths (/adaptive/<id>/insights) and message fields, so only a plain
// identifier is accepted; anything else falls back rather than being half-used.
const USER_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/** Pure: merge an untrusted parsed value over the defaults, field by field. */
export function resolveRuntimeConfig(raw: unknown): RuntimeConfig {
  const cfg = { ...DEFAULT_RUNTIME_CONFIG };
  if (raw && typeof raw === 'object') {
    const id = (raw as Record<string, unknown>).operatorUserId;
    if (typeof id === 'string' && USER_ID.test(id.trim())) cfg.operatorUserId = id.trim();
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
): Promise<RuntimeConfig> {
  try {
    const res = await fetchImpl(url, { cache: 'no-store', signal: AbortSignal.timeout(timeoutMs) });
    // A missing file usually comes back as the SPA fallback (index.html, 200), not a 404, so a
    // non-JSON body is "no config", not an error.
    if (!res.ok) return setRuntimeConfig(null);
    return setRuntimeConfig(await res.json());
  } catch {
    return setRuntimeConfig(null);
  }
}
