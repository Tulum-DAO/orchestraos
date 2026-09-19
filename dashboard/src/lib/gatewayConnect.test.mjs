// RED-first tests for the web first-run gateway-connect logic.
// Run: node --experimental-strip-types dashboard/src/lib/gatewayConnect.test.mjs
import assert from 'node:assert';
import {
  repairGatewayUrl,
  classifyProbe,
  isIdentityShape,
  messageFor,
  probeGateway,
  probeSameOriginSession,
} from './gatewayConnect.ts';

// ---------- repairGatewayUrl: repair, don't reject ----------
{
  const p = repairGatewayUrl('srv1397016.tail8be541.ts.net:8444');
  assert.equal(p.baseUrl, 'https://srv1397016.tail8be541.ts.net:8444');
  assert.equal(p.host, 'srv1397016.tail8be541.ts.net');
  assert.equal(p.port, 8444);
  assert.equal(p.scheme, 'https');
  assert.equal(p.explicitPort, true);
  assert.equal(p.isLan, false);
}
{
  // whitespace + trailing slash + missing scheme
  const p = repairGatewayUrl('   http://myhost.example/   ');
  assert.equal(p.baseUrl, 'http://myhost.example');
  assert.equal(p.port, 80);
  assert.equal(p.explicitPort, false);
}
{
  // no scheme, public host -> https default, port 443 implicit
  const p = repairGatewayUrl('gateway.example.com');
  assert.equal(p.baseUrl, 'https://gateway.example.com');
  assert.equal(p.scheme, 'https');
  assert.equal(p.port, 443);
}
{
  // LAN host -> plain http default, legal
  const p = repairGatewayUrl('localhost:8890');
  assert.equal(p.scheme, 'http');
  assert.equal(p.baseUrl, 'http://localhost:8890');
  assert.equal(p.isLan, true);
}
{
  const p = repairGatewayUrl('192.168.1.50:8890');
  assert.equal(p.scheme, 'http');
  assert.equal(p.isLan, true);
}
{
  // internal whitespace pasted from a chat line
  const p = repairGatewayUrl('https://srv.ts.net :8444');
  assert.equal(p.baseUrl, 'https://srv.ts.net:8444');
}
{
  assert.equal(repairGatewayUrl(''), null);
  assert.equal(repairGatewayUrl('   '), null);
  assert.equal(repairGatewayUrl(null), null);
}

// ---------- isIdentityShape: frozen contract, integer protocol ----------
assert.equal(isIdentityShape({ service: 'orchestraos-gateway', protocol: 1 }), true);
assert.equal(isIdentityShape({ service: 'orchestraos-gateway', protocol: 2, extra: 'ignored' }), true);
assert.equal(isIdentityShape({ service: 'orchestraos-gateway', protocol: true }), false); // bool, not int
assert.equal(isIdentityShape({ service: 'something-else', protocol: 1 }), false);
assert.equal(isIdentityShape({ protocol: 1 }), false);
assert.equal(isIdentityShape('orchestraos-gateway'), false);
assert.equal(isIdentityShape(null), false);

// ---------- classifyProbe: 5 outcomes ----------
const explicit = repairGatewayUrl('host.example:8444');
const implicit = repairGatewayUrl('host.example');
assert.equal(classifyProbe({ kind: 'unreachable' }, explicit), 'NO_ANSWER_ON_PORT');
assert.equal(classifyProbe({ kind: 'unreachable' }, implicit), 'CANT_FIND');
assert.equal(classifyProbe({ kind: 'not-identity' }, explicit), 'NOT_A_GATEWAY');
assert.equal(classifyProbe({ kind: 'identity-ok', capabilitiesStatus: 200 }, explicit), 'CONNECTED');
assert.equal(classifyProbe({ kind: 'identity-ok', capabilitiesStatus: 401 }, explicit), 'BAD_TOKEN');
assert.equal(classifyProbe({ kind: 'identity-ok', capabilitiesStatus: 403 }, explicit), 'BAD_TOKEN');
// caps hiccup (5xx) while identity is fine must NOT read as a bad token
assert.equal(classifyProbe({ kind: 'identity-ok', capabilitiesStatus: 503 }, explicit), 'CONNECTED');

// ---------- messageFor: EXACT verbatim (devex-review canonical msg_c9663cb3), host/port/N substituted ----------
assert.equal(
  messageFor('CANT_FIND', explicit),
  "Can't find host.example. Check the spelling — and if that's a tailnet name, make sure this phone is on the same tailnet.",
);
assert.equal(
  messageFor('NO_ANSWER_ON_PORT', explicit),
  'Found host.example, but nothing is answering on port 8444. Is `orchestra up` running on that machine?',
);
assert.equal(
  messageFor('NOT_A_GATEWAY', explicit),
  "Something is running at host.example:8444, but it isn't an OrchestraOS gateway. Check the port — the gateway is usually 8890, and 8891 is the dashboard.",
);
assert.equal(
  messageFor('BAD_TOKEN', explicit),
  'That is an OrchestraOS gateway, but it didn\'t accept this token. Run `orchestra pair` on the server and scan the new code.',
);
// success: middle-dot separators, gateway v<N> from the identity protocol, em-dash before "they"
assert.equal(
  messageFor('CONNECTED', explicit, 2),
  'Connected to host.example · gateway v2 · no cards yet — they appear here when an agent needs a decision.',
);
assert.equal(
  messageFor('CONNECTED', explicit), // default N=1 when protocol omitted
  'Connected to host.example · gateway v1 · no cards yet — they appear here when an agent needs a decision.',
);

// ---------- probeGateway: async, fetch-stubbed ----------
function res(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}
function stub(handler) {
  // handler(url) -> a Response-like, or 'hang' to never resolve (exercises the abort/timeout path)
  return (url, opts) =>
    new Promise((resolve, reject) => {
      if (opts && opts.signal) {
        if (opts.signal.aborted) return reject(new DOMException('aborted', 'AbortError'));
        opts.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }
      const out = handler(url);
      if (out === 'hang') return; // never resolves -> AbortController fires -> reject
      resolve(out);
    });
}
const P = repairGatewayUrl('host.example:8444');

async function run() {
  // both ok -> CONNECTED with pending
  let r = await probeGateway(P, 'tok', {
    fetchImpl: stub((u) =>
      u.endsWith('/gateway/identity')
        ? res(200, { service: 'orchestraos-gateway', protocol: 1 })
        : res(200, { providers: [], surfaces: ['approvals'], pending: 4 }),
    ),
  });
  assert.equal(r.outcome, 'CONNECTED');
  assert.equal(r.pending, 4);
  assert.equal(r.protocol, 1); // identity protocol threads through for the "gateway v<N>" line

  // identity ok, caps 401 -> BAD_TOKEN
  r = await probeGateway(P, 'bad', {
    fetchImpl: stub((u) =>
      u.endsWith('/gateway/identity') ? res(200, { service: 'orchestraos-gateway', protocol: 1 }) : res(401, {}),
    ),
  });
  assert.equal(r.outcome, 'BAD_TOKEN');

  // answered but wrong shape -> NOT_A_GATEWAY
  r = await probeGateway(P, 'tok', {
    fetchImpl: stub((u) => (u.endsWith('/gateway/identity') ? res(200, { hello: 'world' }) : res(200, {}))),
  });
  assert.equal(r.outcome, 'NOT_A_GATEWAY');

  // identity non-2xx -> NOT_A_GATEWAY
  r = await probeGateway(P, 'tok', { fetchImpl: stub(() => res(404, {})) });
  assert.equal(r.outcome, 'NOT_A_GATEWAY');

  // network failure, explicit port -> NO_ANSWER_ON_PORT
  r = await probeGateway(P, 'tok', {
    fetchImpl: stub(() => {
      throw new TypeError('Failed to fetch');
    }),
  });
  assert.equal(r.outcome, 'NO_ANSWER_ON_PORT');

  // network failure, implicit port -> CANT_FIND
  r = await probeGateway(repairGatewayUrl('host.example'), 'tok', {
    fetchImpl: stub(() => {
      throw new TypeError('Failed to fetch');
    }),
  });
  assert.equal(r.outcome, 'CANT_FIND');

  // timeout on identity (hang) with a tiny cap -> unreachable -> port outcome
  r = await probeGateway(P, 'tok', { timeoutMs: 20, fetchImpl: stub(() => 'hang') });
  assert.equal(r.outcome, 'NO_ANSWER_ON_PORT');

  // ---------- probeSameOriginSession: the section-B ruling ----------
  // Ruling (devex-review, contract owner): a same-origin session with an existing valid
  // session auto-connects silently — never a connect screen. The live connection IS the
  // evidence, so we probe the dashboard's own same-origin authenticated endpoint (/api/me).

  // existing valid cookie session (200) -> true, auto-connect silently
  assert.equal(
    await probeSameOriginSession({
      fetchImpl: stub((u) => (u.endsWith('/api/me') ? res(200, { username: 'shaw', role: 'admin' }) : res(404, {}))),
    }),
    true,
  );

  // no session / stranger (401) -> false, must show the connect screen
  assert.equal(
    await probeSameOriginSession({ fetchImpl: stub(() => res(401, {})) }),
    false,
  );

  // probe MUST NOT trigger the /login redirect on 401 — it does a raw same-origin fetch,
  // not the api.ts wrapper. A stub that has no window means a redirect attempt would throw;
  // returning false cleanly proves no redirect side-effect.
  assert.equal(
    await probeSameOriginSession({ fetchImpl: stub(() => res(403, {})) }),
    false,
  );

  // network error -> false (don't gate a stranger any differently; ConnectScreen is the safe default)
  assert.equal(
    await probeSameOriginSession({
      fetchImpl: stub(() => {
        throw new TypeError('Failed to fetch');
      }),
    }),
    false,
  );

  // timeout (hang) with a tiny cap -> false, never a hung splash
  assert.equal(
    await probeSameOriginSession({ timeoutMs: 20, fetchImpl: stub(() => 'hang') }),
    false,
  );
}

await run();
console.log('gatewayConnect.test.mjs: all assertions passed');
