/**
 * cors-origin.ts — who may read this API from a browser.
 *
 * Split out of server.ts so the decision is testable without standing up express, and so
 * the two faults found on 2026-09-29 stay fixed:
 *
 *  - SAME-ORIGIN IS NOT CROSS-ORIGIN. The allowlist is built from [dashboard] host/port,
 *    which is the loopback address. An install reached at its real address — a Tailscale
 *    name, a tunnel, a reverse proxy — was not in its own allowlist, so every request the
 *    operator made was logged as "blocked" even though the browser never needed the header
 *    that was withheld (the page and the API share an origin). Nothing broke, but the log
 *    said otherwise and triage followed it down the wrong path.
 *
 *  - An install genuinely reached cross-origin at its public address WOULD have failed, so
 *    [public].host — documented as "the externally-reachable URL for this install" — now
 *    counts as an allowed origin.
 *
 * What this does NOT do is reflect any origin back. An unknown origin is still refused.
 */

export interface OriginInputs {
  origin?: string;
  /**
   * X-Forwarded-Host, when a proxy preserved the address the browser actually used.
   * ONLY pass this when the request arrived from a trusted peer: any client can send the
   * header, and trusting it from an arbitrary peer would let a caller nominate its own
   * origin as same-origin (flagged in review, 2026-09-29). The caller decides trust; this
   * module never reads a socket.
   */
  forwardedHost?: string;
  /** The request's own Host header (right when nothing rewrote it). */
  hostHeader?: string;
  allowlist: string[];
}

export interface OriginDecision {
  allow: boolean;
  sameOrigin: boolean;
  reason: 'no-origin' | 'allowlisted' | 'same-origin' | 'not-allowlisted' | 'malformed-origin';
}

/** scheme://host:port of a URL, or null when it is not one. */
function authorityOf(origin: string): string | null {
  try {
    const u = new URL(origin);
    return u.host || null;
  } catch {
    return null;
  }
}

export function originDecision(i: OriginInputs): OriginDecision {
  // CORS is a browser control. A caller that sends no Origin (curl, a server-side client)
  // is not what it defends against, and refusing those breaks every non-browser client
  // without adding protection.
  if (!i.origin) return { allow: true, sameOrigin: false, reason: 'no-origin' };

  if (i.allowlist.includes(i.origin)) {
    return { allow: true, sameOrigin: false, reason: 'allowlisted' };
  }

  const authority = authorityOf(i.origin);
  if (!authority) return { allow: false, sameOrigin: false, reason: 'malformed-origin' };

  // Same-origin: the page lives at the very address this request was sent to. An EXACT
  // authority match — a suffix test would let evil-<host> pass for <host>. Both inputs are
  // client-settable in principle; it is the CALLER's job to have passed a forwardedHost
  // only from a trusted peer (see server.ts), which is why this is stated in the type.
  const addressedAs = (i.forwardedHost || i.hostHeader || '').trim().toLowerCase();
  if (addressedAs && addressedAs === authority.toLowerCase()) {
    return { allow: true, sameOrigin: true, reason: 'same-origin' };
  }

  return { allow: false, sameOrigin: false, reason: 'not-allowlisted' };
}

/**
 * [public].host as browser origins. It is written as a URL or a bare host; a bare host
 * could be served either way, so both schemes count. Any path is dropped — an origin is
 * scheme://host:port and nothing else.
 */
export function publicOrigins(publicHost: string): string[] {
  const raw = (publicHost || '').trim();
  if (!raw) return [];
  if (/^https?:\/\//i.test(raw)) {
    try {
      const u = new URL(raw);
      return [`${u.protocol}//${u.host}`];
    } catch {
      return [];
    }
  }
  const hostOnly = raw.split('/')[0];
  if (!hostOnly || /\s/.test(hostOnly)) return [];
  return [`http://${hostOnly}`, `https://${hostOnly}`];
}
