/**
 * gatewayConfig.ts — where the web app remembers which gateway it paired with.
 *
 * New concept: until now the dashboard was a same-origin app (relative `/api`,
 * cookie/session auth, no client-held token). The web first-run lets a stranger
 * point the dashboard at a gateway URL + paste a token, exactly like the iOS app.
 * When unset, everything falls back to same-origin `/api` — existing internal
 * users are unaffected (additive).
 *
 * Manual localStorage load/save (matches modelSelection.ts / agentSettings.ts —
 * no persist middleware). Read from React via the hook, and from the non-React
 * api.ts wrapper via useGatewayConfig.getState().
 */
import { create } from 'zustand';

interface GatewayConfigState {
  baseUrl: string | null; // normalized origin, no trailing slash; null = same-origin fallback
  token: string | null; // bearer; null = no client bearer (same-origin cookie/session)
  configured: boolean; // a probe has connected at least once
}

interface GatewayConfigStore extends GatewayConfigState {
  connect: (args: { baseUrl: string; token: string }) => void;
  disconnect: () => void;
  // Section-B ruling: a same-origin session with an existing valid session auto-connects
  // silently. This keeps baseUrl/token null (same-origin cookie path) and only marks configured.
  // Deliberately NOT persisted — it is re-proven from the live session on every boot, so the
  // stored config keeps meaning "an explicitly paired gateway" and a logout is never sticky.
  markSameOriginConnected: () => void;
}

const STORAGE_KEY = 'orchestra.gatewayConfig';

function loadInitial(): GatewayConfigState {
  try {
    const item = localStorage.getItem(STORAGE_KEY);
    if (item) {
      const p = JSON.parse(item) as Partial<GatewayConfigState>;
      return {
        baseUrl: typeof p.baseUrl === 'string' ? p.baseUrl : null,
        token: typeof p.token === 'string' ? p.token : null,
        configured: !!p.configured,
      };
    }
  } catch {
    // localStorage unavailable (private mode) — fall through to the same-origin default.
  }
  return { baseUrl: null, token: null, configured: false };
}

function persist(state: GatewayConfigState) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Best-effort — the session still works with the in-memory config.
  }
}

export const useGatewayConfig = create<GatewayConfigStore>((set) => ({
  ...loadInitial(),
  connect: ({ baseUrl, token }) => {
    const next: GatewayConfigState = { baseUrl, token, configured: true };
    persist(next);
    set(next);
  },
  disconnect: () => {
    const next: GatewayConfigState = { baseUrl: null, token: null, configured: false };
    persist(next);
    set(next);
  },
  markSameOriginConnected: () => {
    // In-memory only (no persist): same-origin, cookie-authed, no client bearer.
    set({ baseUrl: null, token: null, configured: true });
  },
}));

/**
 * Bearer header for the dashboard's data calls, if a token is configured.
 *
 * P1 scope: the dashboard's `/api/...` data path stays SAME-ORIGIN (it hits the api
 * server on the PWA's own host); connecting to a gateway on a DIFFERENT origin only
 * validates the handshake + stores the token here. This is correct for the common
 * attendee case where the PWA is served BY the gateway host (`orchestra up`), so
 * same-origin `/api` + this bearer is the paired gateway. Cross-origin data rewiring
 * (a dashboard pointed at a remote gateway) needs CORS on the gateway and is a P2
 * decision (flagged to gm/devex-review), deliberately not attempted here.
 */
export function authHeaders(): Record<string, string> {
  const { token } = useGatewayConfig.getState();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
