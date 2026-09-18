// RED-first tests for the web first-run gateway-connect logic.
// Run: node --experimental-strip-types dashboard/src/lib/gatewayConnect.test.mjs
import assert from 'node:assert';
import {
  repairGatewayUrl,
  classifyProbe,
  isIdentityShape,
  messageFor,
  probeGateway,
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

// ---------- messageFor: each failure echoes host and port; success is non-empty ----------
assert.match(messageFor('CANT_FIND', explicit), /Can't find host\.example\./);
assert.match(messageFor('CANT_FIND', explicit), /same tailnet/);
assert.match(messageFor('NO_ANSWER_ON_PORT', explicit), /nothing is answering on port 8444/);
assert.match(messageFor('NO_ANSWER_ON_PORT', explicit), /orchestra up/);
assert.match(messageFor('NOT_A_GATEWAY', explicit), /host\.example:8444/);
assert.match(messageFor('NOT_A_GATEWAY', explicit), /8890.*8891|8890/);
assert.match(messageFor('BAD_TOKEN', explicit), /didn't accept this token/);
assert.match(messageFor('BAD_TOKEN', explicit), /orchestra pair/);
assert.match(messageFor('CONNECTED', explicit), /Connected to host\.example/);
assert.match(messageFor('CONNECTED', explicit), /no cards yet/);

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
}

await run();
console.log('gatewayConnect.test.mjs: all assertions passed');
