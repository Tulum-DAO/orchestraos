/**
 * Who a reaction is from (spec "Reactor identity"). An agent ONLY with X-Orchestra-Agent AND the matching
 * X-Orchestra-Agent-Token; a header WITHOUT a valid token is REJECTED (AGENT_TOKEN_INVALID, the route answers 401; never
 * recorded as the operator). No agent header at all: the principal (on this box, the configured operator). No trust from loopback or a missing login header.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/lib/reactor.test.ts   (from api/)
 */
import { testTmpDir } from './testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import type { Request } from 'express';

const dir = testTmpDir('reactor-');
const TOKEN_FILE = join(dir, 'agent-react-token');
writeFileSync(TOKEN_FILE, 'tok-reactor-1\n', { mode: 0o600 });
process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE = TOKEN_FILE;
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
after(() => rmSync(dir, { recursive: true, force: true }));

const { reactorOf, AGENT_TOKEN_INVALID } = await import('./reactor.js');
const { loadConfig } = await import('./config.js');
const OP = loadConfig().operatorId;
const req = (headers: Record<string, string> = {}) => ({ headers }) as unknown as Request;
const user = { kind: 'user', id: OP };

test('no headers: the operator', () => {
  assert.deepEqual(reactorOf(req()), user);
});

test('agent header + the right token: that agent', () => {
  assert.deepEqual(reactorOf(req({ 'x-orchestra-agent': 'pm-x', 'x-orchestra-agent-token': 'tok-reactor-1' })),
    { kind: 'agent', id: 'pm-x' });
});

test('an agent header WITHOUT a valid token is rejected, never the operator', () => {
  assert.equal(reactorOf(req({ 'x-orchestra-agent': 'pm-x' })), AGENT_TOKEN_INVALID, 'no token');
  assert.equal(reactorOf(req({ 'x-orchestra-agent': 'pm-x', 'x-orchestra-agent-token': 'tok-reactor-2' })), AGENT_TOKEN_INVALID,
    'same length, wrong');
  assert.equal(reactorOf(req({ 'x-orchestra-agent': 'pm-x', 'x-orchestra-agent-token': 'nope' })), AGENT_TOKEN_INVALID, 'wrong');
  assert.deepEqual(reactorOf(req({ 'x-orchestra-agent-token': 'tok-reactor-1' })), user, 'token alone: no agent header, the operator');
});

test('a malformed agent id is rejected even with the token', () => {
  for (const id of ['../etc', 'a b', '-lead']) {
    assert.equal(reactorOf(req({ 'x-orchestra-agent': id, 'x-orchestra-agent-token': 'tok-reactor-1' })), AGENT_TOKEN_INVALID, id);
  }
  assert.deepEqual(reactorOf(req({ 'x-orchestra-agent': '', 'x-orchestra-agent-token': 'tok-reactor-1' })), user, 'empty header = absent');
});

test('no token file: nobody is an agent', () => {
  process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE = join(dir, 'missing');
  try {
    assert.deepEqual(reactorOf(req({ 'x-orchestra-agent': 'pm-x', 'x-orchestra-agent-token': 'tok-reactor-1' })), AGENT_TOKEN_INVALID);
  } finally { process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE = TOKEN_FILE; }
});

test('trusted-proxy mode stays fail-closed: no identity -> null (the route answers 401)', () => {
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try {
    assert.equal(reactorOf(req()), null);
    assert.equal(reactorOf(req({ 'x-orchestra-agent': 'pm-x', 'x-orchestra-agent-token': 'tok-reactor-1' })), null,
      'an agent still needs a principal to act through');
    assert.deepEqual(reactorOf(req({ 'x-orchestra-user': 'eve', 'x-orchestra-role': 'admin' })), { kind: 'user', id: 'eve' });
  } finally { delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS; }
});

test("a latin1 (non-ASCII) token header is rejected, not a crash", () => {
  writeFileSync(TOKEN_FILE, "t".repeat(13) + "\n", { mode: 0o600 });
  try {
    assert.deepEqual(reactorOf(req({ "x-orchestra-agent": "pm-x", "x-orchestra-agent-token": "t".repeat(12) + "é" })), AGENT_TOKEN_INVALID);
  } finally { writeFileSync(TOKEN_FILE, "tok-reactor-1\n", { mode: 0o600 }); }
});
