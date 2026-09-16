/**
 * OrchestraOS V2 assistant — build/runtime config + feature flag.
 *
 * FEATURE FLAG (rollback): the /assistant route and nav link only exist
 * when VITE_ASSISTANT_V2 === "1" at build time. A build without it ships
 * zero assistant surface — that IS the rollback (rebuild without the flag,
 * or revert this branch). The route is additive and never touches the
 * existing live chat bubble.
 */

/** True when the V2 assistant surface should be exposed. */
export const ASSISTANT_V2_ENABLED = import.meta.env.VITE_ASSISTANT_V2 === '1';

/**
 * True when the bottom-right bubble (JarvisPanel) should render the NEW /v2
 * converse UI instead of the legacy Jarvis API. Independent of
 * ASSISTANT_V2_ENABLED so the bubble rewire can roll back on its own — flag
 * OFF keeps the original working JarvisPanel untouched (never break the operator's
 * dashboard access). Rollback = build without VITE_ASSISTANT_BUBBLE_V2.
 */
export const ASSISTANT_BUBBLE_V2_ENABLED = import.meta.env.VITE_ASSISTANT_BUBBLE_V2 === '1';

/**
 * Endpoint the converse UI POSTs to. In prod the combo-proxy routes
 * /api/jarvis/* → 127.0.0.1:5060 (real Jarvis /v2). For B1 the mock
 * server handles this same path, so nothing changes between mock + prod.
 */
export const CONVERSE_ENDPOINT =
  (import.meta.env.VITE_JARVIS_CONVERSE_URL as string | undefined) ||
  '/api/jarvis/v2/converse';

/**
 * Jarvis auth token sent as X-Jarvis-Token. For B1/dev this comes from a
 * build env; server validates against ~/.config/jarvis/token. NOTE: the
 * design flags client-held tokens — prod wiring of a real per-user token
 * is a later concern; auth being ON (day-1) is what B1 must demonstrate.
 */
export const JARVIS_TOKEN =
  (import.meta.env.VITE_JARVIS_TOKEN as string | undefined) || 'dev-mock-token';

/** Default user identity for the dashboard bubble surface. */
export const DEFAULT_USER = 'operator';

/** Rendering surface for the dashboard converse UI (never scopes state). */
export const DEFAULT_CHANNEL = 'bubble';
