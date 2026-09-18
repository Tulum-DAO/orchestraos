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
  | 'NOT_A_GATEWAY' // answered, but /gateway/identity is not the frozen shape
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

// The five verbatim sentences (devex-review). Each failure echoes the host and port the user typed.
export function messageFor(outcome: ConnectOutcome, p: ParsedGateway): string {
  switch (outcome) {
    case 'CANT_FIND':
      return `Can't find ${p.host}. Check the spelling — and if that's a tailnet name, make sure this device is on the same tailnet.`;
    case 'NO_ANSWER_ON_PORT':
      return `Found ${p.host}, but nothing is answering on port ${p.port}. Is orchestra up running on that machine?`;
    case 'NOT_A_GATEWAY':
      return `Something is running at ${p.host}:${p.port}, but it isn't an OrchestraOS gateway. Check the port — the gateway is usually 8890, and 8891 is the dashboard.`;
    case 'BAD_TOKEN':
      return `That is an OrchestraOS gateway, but it didn't accept this token. Run orchestra pair on the server and paste the new code.`;
    case 'CONNECTED':
      return `Connected to ${p.host} — gateway v1 — no cards yet. They appear here when an agent needs a decision.`;
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
): Promise<{ outcome: ConnectOutcome; pending?: number; capabilities?: unknown }> {
  const timeoutMs = opts.timeoutMs ?? 5000;
  const f = opts.fetchImpl ?? fetch;

  // 1) identity (unauthenticated)
  let identityBody: unknown;
  try {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    const r = await f(`${parsed.baseUrl}/gateway/identity`, { signal: ac.signal });
    clearTimeout(t);
    if (!r.ok) return { outcome: 'NOT_A_GATEWAY' };
    try {
      identityBody = await r.json();
    } catch {
      return { outcome: 'NOT_A_GATEWAY' };
    }
  } catch {
    return { outcome: classifyProbe({ kind: 'unreachable' }, parsed) };
  }
  if (!isIdentityShape(identityBody)) return { outcome: 'NOT_A_GATEWAY' };

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
      return { outcome, pending, capabilities: caps };
    }
    return { outcome };
  } catch {
    // identity was fine but caps timed out — the gateway is reachable; ask for a re-probe via BAD_TOKEN
    // path is misleading, but the honest state is "gateway there, caps unreachable". Surface bad-token
    // is wrong; treat as connected-unknown so the user isn't bounced to re-pair. We choose CONNECTED
    // with pending unknown rather than a false auth error.
    return { outcome: 'CONNECTED' };
  }
}
