import { testTmpDir } from './testTmp.js';
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

const dir = testTmpDir('orchestra-terminal-relay-');
const secretFile = join(dir, 'secret');
writeFileSync(secretFile, 'relay-test-secret\n');
process.env.TERMINAL_RELAY_SECRET_FILE = secretFile;

function req(value?: string) {
  return { headers: value === undefined ? {} : { 'x-orchestra-terminal-relay': value } };
}

test('terminal relay credential accepts only an exact reference-held value', async () => {
  const { isTerminalRelay } = await import('./terminal-relay-secret.js');
  assert.equal(isTerminalRelay(req('relay-test-secret')), true);
  assert.equal(isTerminalRelay(req()), false);
  assert.equal(isTerminalRelay(req('wrong')), false);
  assert.equal(isTerminalRelay(req('relay-test-secret-x')), false);
});

test("terminal relay: a latin1 value of equal string length is rejected, not thrown", async () => {
  const { isTerminalRelay } = await import("./terminal-relay-secret.js");
  const expected = "relay-test-secret";
  const bad = expected.slice(0, -1) + "é";
  assert.equal(bad.length, expected.length);
  assert.doesNotThrow(() => isTerminalRelay(req(bad)));
  assert.equal(isTerminalRelay(req(bad)), false);
});
