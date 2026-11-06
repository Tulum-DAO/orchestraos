/**
 * The agent react token (<data dir>/state/agent-react-token, 0600): created at API boot when missing, never
 * replaced once it has a value, and matched in constant time. An agent proves who it is with it
 * (lib/reactor.ts); without it an X-Orchestra-Agent header means nothing.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/lib/agent-react-token.test.ts   (from api/)
 */
import { testTmpDir } from './testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, readFileSync, statSync, chmodSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('agent-react-token-');
after(() => rmSync(dir, { recursive: true, force: true }));
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}

const { ensureAgentReactToken, readAgentReactToken, agentTokenMatches, agentReactTokenFile } =
  await import('./agent-react-token.js');

const mode = (f: string) => statSync(f).mode & 0o777;

test('missing: created as 64 hex chars, mode 0600, parent dirs made', () => {
  const f = join(dir, 'a', 'state', 'agent-react-token');
  const tok = ensureAgentReactToken(f);
  assert.match(tok, /^[0-9a-f]{64}$/);
  assert.equal(readFileSync(f, 'utf-8').trim(), tok);
  assert.equal(mode(f), 0o600);
});

test('present: kept as it is (a second boot never rotates it)', () => {
  const f = join(dir, 'b-token');
  const first = ensureAgentReactToken(f);
  const second = ensureAgentReactToken(f);
  assert.equal(second, first);
  assert.equal(readAgentReactToken(f), first);
});

test('present but too open: tightened to 0600, value kept', () => {
  const f = join(dir, 'c-token');
  writeFileSync(f, 'kept-value\n');
  chmodSync(f, 0o644);
  assert.equal(ensureAgentReactToken(f), 'kept-value');
  assert.equal(mode(f), 0o600);
});

test('present but empty: replaced with a fresh token', () => {
  const f = join(dir, 'd-token');
  writeFileSync(f, '');
  const tok = ensureAgentReactToken(f);
  assert.match(tok, /^[0-9a-f]{64}$/);
  assert.equal(readAgentReactToken(f), tok);
  assert.equal(mode(f), 0o600);
});

test('agentTokenMatches: only the exact value', () => {
  const f = join(dir, 'e-token');
  writeFileSync(f, 'secret-abc\n', { mode: 0o600 });
  assert.equal(agentTokenMatches('secret-abc', f), true);
  assert.equal(agentTokenMatches('', f), false);
  assert.equal(agentTokenMatches('secret-abd', f), false, 'same length, wrong');
  assert.equal(agentTokenMatches('secret-ab', f), false, 'a prefix');
  assert.equal(agentTokenMatches('secret-abcd', f), false, 'longer');
  assert.equal(agentTokenMatches('secret-abc', join(dir, 'no-such-file')), false, 'no file: nothing matches');
});

test('default path: <data dir>/state/agent-react-token, or the _FILE override', () => {
  const prevDir = process.env.ORCHESTRA_DIR;
  const prevFile = process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE;
  try {
    delete process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE;
    process.env.ORCHESTRA_DIR = join(dir, 'data');
    assert.equal(agentReactTokenFile(), join(dir, 'data', 'state', 'agent-react-token'));
    process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE = join(dir, 'override');
    assert.equal(agentReactTokenFile(), join(dir, 'override'));
  } finally {
    if (prevDir === undefined) delete process.env.ORCHESTRA_DIR; else process.env.ORCHESTRA_DIR = prevDir;
    if (prevFile === undefined) delete process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE;
    else process.env.ORCHESTRA_AGENT_REACT_TOKEN_FILE = prevFile;
  }
});

test("agentTokenMatches: a latin1 value of equal string length but different byte length is false, never a throw", () => {
  const f = join(dir, "f-token");
  writeFileSync(f, "a".repeat(64) + "\n", { mode: 0o600 });
  assert.doesNotThrow(() => agentTokenMatches("a".repeat(63) + "é", f));
  assert.equal(agentTokenMatches("a".repeat(63) + "é", f), false);
});
