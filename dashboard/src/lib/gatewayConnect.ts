// Web first-run "connect to gateway" logic — pure + the async probe.
//
// Contract (devex-review ruling msg_b1135ce2, adopted verbatim):
//  - IDENTITY   GET {base}/gateway/identity   UNAUTH, frozen: {"service":"orchestraos-gateway","protocol":1}
//  - CAPS       GET {base}/gateway/capabilities  behind the bearer, additive
//  - Repair inputs, do not reject: host, host:port, http://host, https://host, trailing slash, whitespace.
//  - Four probe outcomes -> five first-run states (four failure sentences + the connected state).
//  - Probe capped at ~5s so an off-host fails into a sentence, never a spinner.
//  - Plain http on a LAN is legal (one quiet line, no modal).
//
// Browser limitation (stated honestly per devex-review "say so in words"): a browser fetch
// cannot tell DNS-failure from connection-refused from CORS — all surface as one opaque
// TypeError with no status. So the two "can't reach" sentences cannot be split from the web
// the way the native iOS app can. We map an unreachable/timed-out probe to the CANT_FIND
// sentence by default, and to NO_ANSWER_ON_PORT only when the user typed an explicit
// non-default port (the one signal we do have). See classifyProbe.

export interface ParsedGateway {
  baseUrl: string; // normalized origin, no trailing slash, e.g. "https://host:8444"
  host: string;
  port: number; // explicit or scheme default (443/80)
  scheme: 'http' | 'https';
  explicitPort: boolean;
  isLan: boolean; // localhost / *.local / RFC1918 — plain http is legal & expected here
}

const LAN_RE =
  /^(localhost|127(?:\.\d{1,3}){3}|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|[^.]+\.local)$/i;

function isLanHost(host: string): boolean {
  return LAN_RE.test(host);
}

/**
 * Repair a user-typed gateway address into a normalized base URL — never reject a fixable input.
 * Returns null only for genuinely empty input. Throws only for input that cannot be a URL at all.
 */
export function repairGatewayUrl(raw: string): ParsedGateway | null {
  if (raw == null) return null;
  let s = String(raw).trim();
  if (!s) return null;
  s = s.replace(/\s+/g, ''); // a pasted address never contains internal whitespace

  // Add a scheme if missing. Default https, except LAN hosts which are plain-http by convention.
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) {
    const hostPart = s.split('/')[0].split(':')[0];
    s = (isLanHost(hostPart) ? 'http://' : 'https://') + s;
  }

  let u: URL;
  try {
    u = new URL(s);
  } catch {
    throw new Error('unparseable');
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') throw new Error('unsupported scheme');

  const scheme = u.protocol === 'http:' ? 'http' : 'https';
  const explicitPort = u.port !== '';
  const port = explicitPort ? Number(u.port) : scheme === 'https' ? 443 : 80;
  const host = u.hostname;
  const baseUrl = `${scheme}://${host}${explicitPort ? ':' + port : ''}`;
  return { baseUrl, host, port, scheme, explicitPort, isLan: isLanHost(host) };
}

// The five first-run states.
export type ConnectOutcome =
  | 'CANT_FIND' // no answer, host cannot be reached (default for opaque browser failure)
  | 'NO_ANSWER_ON_PORT' // host reached but the port is refused (best-effort from the web)
  | 'NOT_A_GATEWAY' // answered, but neither the frozen identity NOR a legacy OrchestraOS /health
  | 'PRE_HANDSHAKE' // a REAL OrchestraOS gateway whose build predates the handshake (identity 404 + legacy /health)
  | 'BAD_TOKEN' // identity ok, capabilities returned 401
  | 'CONNECTED'; // identity ok + capabilities ok

// Discriminated probe result the async probe produces; classifyProbe maps it (+ the parsed
// address) to an outcome. Kept separate so the mapping is pure and unit-testable.
export type ProbeResult =
  | { kind: 'unreachable' } // fetch threw or timed out (opaque)
  | { kind: 'not-identity' } // identity fetch returned, but not the frozen shape (or non-2xx)
  | { kind: 'identity-ok'; capabilitiesStatus: number }; // identity matched; caps fetch status

export function isIdentityShape(body: unknown): boolean {
  if (!body || typeof body !== 'object') return false;
  const b = body as Record<string, unknown>;
  // protocol is an INTEGER, never a bool (typeof true === 'boolean', so the number check excludes it).
  return b.service === 'orchestraos-gateway' && typeof b.protocol === 'number';
}

// The legacy /health shape ({ok, pending, ...}) — the fingerprint of a REAL OrchestraOS gateway
// whose build predates the handshake. Used to tell a pre-handshake gateway (sentence 5) from a
// server that simply isn't a gateway (sentence 3). Heuristic, stated as such: `ok` present +
// no gateway identity. (devex-review FINAL msg_8073f7ec: identity 404 AND legacy /health.)
export function isLegacyHealth(body: unknown): boolean {
  if (!body || typeof body !== 'object') return false;
  return 'ok' in (body as Record<string, unknown>);
}

export function classifyProbe(result: ProbeResult, parsed: ParsedGateway): ConnectOutcome {
  switch (result.kind) {
    case 'unreachable':
      // Browser can't split DNS vs port; use the one signal we have — an explicit non-default port
      // means the user is pointing at a specific listener, so lean "nothing on that port".
      return parsed.explicitPort ? 'NO_ANSWER_ON_PORT' : 'CANT_FIND';
    case 'not-identity':
      return 'NOT_A_GATEWAY';
    case 'identity-ok':
      // Only an auth code (401/403) means "re-pair". Any other non-200 (caps 5xx/unreachable) means
      // the gateway IS there but caps couldn't answer — don't bounce the user to re-pair; treat as
      // connected (pending unknown) so a caps hiccup never reads as a bad token.
      return result.capabilitiesStatus === 401 || result.capabilitiesStatus === 403
        ? 'BAD_TOKEN'
        : 'CONNECTED';
  }
}

/**
 * Section-B ruling (devex-review, contract owner): the connect gate applies to unconfigured
 * sessions with NO existing session only. A same-origin session with an existing VALID session
 * auto-connects silently — the live connection IS the evidence, and the dashboard on the projector
 * must never regress into a connect screen.
 *
 * We test "existing valid session" by probing the dashboard's own same-origin authenticated
 * endpoint (`/api/me`) with the browser's cookie session. A 200 means a live authenticated
 * session exists → auto-connect. Anything else (401/403 stranger, network error, timeout) →
 * false, so ConnectScreen is the safe default.
 *
 * This is a RAW fetch, deliberately NOT the api.ts wrapper: the wrapper redirects to /login on
 * 401, which would be wrong here — a stranger with no session should land on ConnectScreen, not a
 * legacy login page. `credentials: 'same-origin'` so the cookie rides along. Capped so a hung
 * endpoint never freezes the boot on a blank splash.
 */
export async function probeSameOriginSession(
  opts: { timeoutMs?: number; fetchImpl?: typeof fetch } = {},
): Promise<boolean> {
  const timeoutMs = opts.timeoutMs ?? 5000;
  const f = opts.fetchImpl ?? fetch;
  try {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    const r = await f('/api/me', { signal: ac.signal, credentials: 'same-origin' });
    clearTimeout(t);
    return r.status === 200;
  } catch {
    return false;
  }
}

// The five verbatim sentences (devex-review canonical, msg_c9663cb3). Substitute only <host>,
// <port>, and <N> (the identity protocol). The two words "phone" (s1) and "scan" (s4) are
// devex-review's exact wording carried from the shared iOS+web block; on THIS web screen they
// read as iOS-isms ("device"/"paste" would be accurate) — kept verbatim per the explicit
// "substitute only host/port" instruction and flagged to devex-review for a web-adaptation ruling.
export function messageFor(outcome: ConnectOutcome, p: ParsedGateway, protocol = 1): string {
  switch (outcome) {
    case 'CANT_FIND':
      return `Can't find ${p.host}. Check the spelling — and if that's a tailnet name, make sure this device is on the same tailnet.`;
    case 'NO_ANSWER_ON_PORT':
      return `Found ${p.host}, but nothing is answering on port ${p.port}. Is \`orchestra up\` running on that machine?`;
    case 'NOT_A_GATEWAY':
      return `Something is running at ${p.host}:${p.port}, but it isn't an OrchestraOS gateway. Check the port — the gateway is usually 8890, and 8891 is the dashboard.`;
    case 'PRE_HANDSHAKE':
      return `Found an OrchestraOS gateway at ${p.host}:${p.port}. This version predates device pairing, so there's nothing to connect to yet.`;
    case 'BAD_TOKEN':
      return `That is an OrchestraOS gateway, but it didn't accept this token. Run \`orchestra pair\` on the server and use the new code.`;
    case 'CONNECTED':
      return `Connected to ${p.host} · gateway v${protocol} · no cards yet — they appear here when an agent needs a decision.`;
  }
}

/**
 * Probe a gateway: identity (unauth) then capabilities (bearer), capped at timeoutMs.
 * Returns the classified outcome plus the pending count when connected. Never throws for a
 * network failure — that IS the CANT_FIND/port outcome.
 */
export async function probeGateway(
  parsed: ParsedGateway,
  token: string,
  opts: { timeoutMs?: number; fetchImpl?: typeof fetch } = {},
): Promise<{ outcome: ConnectOutcome; pending?: number; capabilities?: unknown; protocol?: number }> {
  const timeoutMs = opts.timeoutMs ?? 5000;
  const f = opts.fetchImpl ?? fetch;

  // Degrade helper: identity answered but wasn't the frozen gateway shape. Probe legacy /health
  // to tell a PRE-HANDSHAKE OrchestraOS gateway (sentence 5) from a server that isn't a gateway
  // (sentence 3) — so a real gateway on the pre-handshake release SHA is never called "not a gateway".
  const degradeViaHealth = async (): Promise<ConnectOutcome> => {
    try {
      const ac = new AbortController();
      const t = setTimeout(() => ac.abort(), timeoutMs);
      const r = await f(`${parsed.baseUrl}/health`, { signal: ac.signal });
      clearTimeout(t);
      if (!r.ok) return 'NOT_A_GATEWAY';
      let body: unknown;
      try { body = await r.json(); } catch { return 'NOT_A_GATEWAY'; }
      return isLegacyHealth(body) ? 'PRE_HANDSHAKE' : 'NOT_A_GATEWAY';
    } catch {
      return 'NOT_A_GATEWAY';
    }
  };

  // 1) identity (unauthenticated)
  let identityBody: unknown;
  try {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    const r = await f(`${parsed.baseUrl}/gateway/identity`, { signal: ac.signal });
    clearTimeout(t);
    if (!r.ok) return { outcome: await degradeViaHealth() };
    try {
      identityBody = await r.json();
    } catch {
      return { outcome: await degradeViaHealth() };
    }
  } catch {
    return { outcome: classifyProbe({ kind: 'unreachable' }, parsed) };
  }
  if (!isIdentityShape(identityBody)) return { outcome: await degradeViaHealth() };
  const protocol = (identityBody as { protocol: number }).protocol; // for the "gateway v<N>" success line

  // 2) capabilities (behind the bearer)
  try {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    const r = await f(`${parsed.baseUrl}/gateway/capabilities`, {
      signal: ac.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    clearTimeout(t);
    const outcome = classifyProbe({ kind: 'identity-ok', capabilitiesStatus: r.status }, parsed);
    if (outcome === 'CONNECTED') {
      let caps: unknown = undefined;
      try {
        caps = await r.json();
      } catch {
        caps = undefined;
      }
      const pending =
        caps && typeof caps === 'object' && typeof (caps as any).pending === 'number'
          ? (caps as any).pending
          : undefined;
      return { outcome, pending, capabilities: caps, protocol };
    }
    return { outcome, protocol };
  } catch {
    // identity was fine but caps timed out — the gateway is reachable; ask for a re-probe via BAD_TOKEN
    // path is misleading, but the honest state is "gateway there, caps unreachable". Surface bad-token
    // is wrong; treat as connected-unknown so the user isn't bounced to re-pair. We choose CONNECTED
    // with pending unknown rather than a false auth error.
    return { outcome: 'CONNECTED', protocol };
  }
}
